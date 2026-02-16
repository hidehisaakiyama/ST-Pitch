import csv
import json
import math
import os
import time
from io import StringIO
from flask import Blueprint, render_template, request, redirect, url_for, current_app, jsonify, flash, Response
from .models import db, MatchResult, EventData, EventSequence
from sqlalchemy import text, func, or_, and_
from geoalchemy2.functions import ST_Contains, ST_Intersects, ST_DWithin
from geoalchemy2 import WKTElement
from werkzeug.utils import secure_filename

main = Blueprint('main', __name__)

@main.route('/')
def index():
    from .models import Tournament, EventData
    
    tournaments = Tournament.query.order_by(Tournament.year.desc()).all()
    
    # 登録済みのmatch_id数を取得（件数表示用）
    match_data = db.session.query(EventData.match_id).distinct().all()
    
    # EventSequenceの統計情報を取得
    total_sequences = EventSequence.query.count()
    sequence_matches = db.session.query(EventSequence.match_id).distinct().count()
    
    return render_template('index.html', 
                         tournaments=tournaments, 
                         match_data=match_data,
                         total_sequences=total_sequences,
                         sequence_matches=sequence_matches)

def parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in ["true", "1", "yes"]:
            return True
        elif value in ["false", "0", "no"]:
            return False
    return None

def generate_event_sequences(match_id):
    """イベントデータからシーケンスを生成してEventSequenceテーブルに保存"""
    try:
        # 既存のシーケンスを削除（重複回避）
        deleted_count = EventSequence.query.filter_by(match_id=match_id).delete()
        if deleted_count > 0:
            print(f"🗑️ 既存シーケンス削除: {match_id} ({deleted_count}個)")
        
        # 時系列順でイベントを取得
        events = EventData.query.filter_by(match_id=match_id).order_by(EventData.time1).all()
        
        if not events:
            raise Exception(f"イベントデータが見つかりません (match_id: {match_id})")
        
        # チーム別にシーケンスを生成
        teams = set(event.side1 for event in events if event.side1)
        if not teams:
            raise Exception(f"有効なチーム情報がありません (match_id: {match_id})")
        
        total_sequences = 0
        for team in teams:
            sequences = analyze_team_sequences(events, team)
            
            # シーケンスをデータベースに保存
            team_sequences = 0
            for seq_num, sequence in enumerate(sequences, 1):
                if len(sequence) >= 2:  # 2つ以上のイベントで構成されるシーケンスのみ
                    save_sequence(match_id, team, seq_num, sequence)
                    team_sequences += 1
            
            total_sequences += team_sequences
            print(f"📈 {team}: {team_sequences}個のシーケンスを生成")
        
        db.session.commit()
        print(f"✅ {match_id}: 合計{total_sequences}個のシーケンスを生成完了")
        
        return total_sequences
        
    except Exception as e:
        db.session.rollback()
        error_msg = f"シーケンス生成エラー (match_id: {match_id}): {str(e)}"
        print(f"❌ {error_msg}")
        raise Exception(error_msg)

def analyze_team_sequences(all_events, team):
    """チームのイベントを連続シーケンスに分析（フロントエンドロジックと同じ条件）"""
    # 該当チームのイベントのみ抽出
    team_events = [event for event in all_events if event.side1 == team]
    if not team_events:
        return []
    
    sequences = []
    
    for i, selected_event in enumerate(team_events):
        # 各イベントを起点とした攻撃シーケンスを抽出
        sequence = extract_attack_sequence_for_event(all_events, selected_event, team)
        
        # 重複チェック: 既存のシーケンスと重複していないか確認
        is_duplicate = False
        for existing_seq in sequences:
            if any(event.id == selected_event.id for event in existing_seq):
                is_duplicate = True
                break
        
        # 重複していない場合のみ追加（2つ以上のイベントで構成される場合）
        if not is_duplicate and len(sequence) >= 2:
            sequences.append(sequence)
    
    return sequences


def extract_attack_sequence_for_event(all_events, selected_event, team):
    """指定されたイベントを含む攻撃シーケンスを抽出（フロントエンドと同じロジック）"""
    # 同じ試合のイベントのみを対象
    match_events = [e for e in all_events if e.match_id == selected_event.match_id]
    match_events.sort(key=lambda x: x.time1)
    
    selected_index = None
    for idx, event in enumerate(match_events):
        if event.id == selected_event.id:
            selected_index = idx
            break
    
    if selected_index is None:
        return [selected_event]
    
    # 攻撃シーケンスの開始を見つける（後方検索）
    sequence_start = selected_index
    for i in range(selected_index - 1, -1, -1):
        event = match_events[i]
        
        # リセット条件：不自然なInterception（得点後の戻し処理など）
        if (event.type == "Interception" and event.mode1 == "play_on" and 
            event.x2 == 0.0 and event.y2 == 0.0):
            break
        
        if event.side1 == team:
            sequence_start = i
        elif event.side1 and event.side1 != team:
            break
    
    # 攻撃シーケンスの終了を見つける（前方検索）
    sequence_end = selected_index
    for i in range(selected_index + 1, len(match_events)):
        event = match_events[i]
        
        # リセット条件：不自然なInterception
        if (event.type == "Interception" and event.mode1 == "play_on" and 
            event.x2 == 0.0 and event.y2 == 0.0):
            break
        
        # ボールが相手に渡った、またはneutralになった場合
        if event.side2 and event.side2 != team:
            sequence_end = i
            break
        elif event.side1 and event.side1 != team:
            break
        elif event.side1 == team:
            sequence_end = i
    
    # シーケンスを抽出（同じチームのイベントのみ）
    sequence = []
    for i in range(sequence_start, sequence_end + 1):
        event = match_events[i]
        if event.side1 == team:
            sequence.append(event)
    
    return sequence

def save_sequence(match_id, team, sequence_number, events):
    """イベントシーケンスをGIS情報付きでデータベースに保存"""
    import json
    from geoalchemy2 import WKTElement
    
    if not events:
        return
    
    start_event = events[0]
    end_event = events[-1]
    
    # GIS情報を生成
    gis_data = calculate_sequence_gis_data(events)
    
    sequence = EventSequence(
        match_id=match_id,
        team=team,
        sequence_number=sequence_number,
        start_time=int(start_event.time1),
        end_time=int(end_event.time1),
        event_count=len(events),
        event_ids=json.dumps([event.id for event in events]),
        # GIS情報を設定
        trajectory=gis_data['trajectory'],
        start_point=gis_data['start_point'],
        end_point=gis_data['end_point'],
        coverage_area=gis_data['coverage_area']
    )
    
    db.session.add(sequence)
    print(f"💾 シーケンス保存: {team} #{sequence_number} ({len(events)}イベント, {start_event.time1}-{end_event.time1}秒) [GIS情報付き]")

def calculate_sequence_gis_data(events):
    """シーケンスのGIS情報（trajectory, start_point, end_point, coverage_area）を計算"""
    from geoalchemy2 import WKTElement
    
    if not events:
        return {'trajectory': None, 'start_point': None, 'end_point': None, 'coverage_area': None}
    
    try:
        # 座標を収集
        valid_points = []
        trajectory_coords = []
        
        for event in events:
            if event.x1 is not None and event.y1 is not None:
                point_coords = (event.x1, event.y1)
                valid_points.append(point_coords)
                trajectory_coords.append(f"{event.x1} {event.y1}")
                
                # 移動先座標も追加
                if event.x2 is not None and event.y2 is not None:
                    end_coords = (event.x2, event.y2)
                    valid_points.append(end_coords)
                    trajectory_coords.append(f"{event.x2} {event.y2}")
        
        if len(valid_points) < 1:
            return {'trajectory': None, 'start_point': None, 'end_point': None, 'coverage_area': None}
        
        # 開始地点と終了地点
        start_coords = valid_points[0]
        end_coords = valid_points[-1]
        
        start_point_wkt = f"SRID=4326;POINT({start_coords[0]} {start_coords[1]})"
        end_point_wkt = f"SRID=4326;POINT({end_coords[0]} {end_coords[1]})"
        
        # 軌跡（LINESTRING）
        trajectory_wkt = None
        if len(trajectory_coords) >= 2:
            # 重複する連続する座標を削除
            unique_coords = []
            for coord in trajectory_coords:
                if not unique_coords or unique_coords[-1] != coord:
                    unique_coords.append(coord)
            
            if len(unique_coords) >= 2:
                trajectory_wkt = f"SRID=4326;LINESTRING({', '.join(unique_coords)})"
        
        # 通過エリア（凸包の代わりに境界矩形）
        coverage_area_wkt = None
        if len(valid_points) >= 3:
            # 重複を削除
            unique_points = list(dict.fromkeys(valid_points))
            if len(unique_points) >= 3:
                x_coords = [x for x, y in unique_points]
                y_coords = [y for x, y in unique_points]
                
                min_x, max_x = min(x_coords), max(x_coords)
                min_y, max_y = min(y_coords), max(y_coords)
                
                # 最小サイズを確保
                if abs(max_x - min_x) < 1:
                    center_x = (min_x + max_x) / 2
                    min_x, max_x = center_x - 0.5, center_x + 0.5
                if abs(max_y - min_y) < 1:
                    center_y = (min_y + max_y) / 2
                    min_y, max_y = center_y - 0.5, center_y + 0.5
                
                # 矩形ポリゴン
                coverage_area_wkt = f"SRID=4326;POLYGON(({min_x} {min_y}, {max_x} {min_y}, {max_x} {max_y}, {min_x} {max_y}, {min_x} {min_y}))"
        
        return {
            'trajectory': WKTElement(trajectory_wkt) if trajectory_wkt else None,
            'start_point': WKTElement(start_point_wkt),
            'end_point': WKTElement(end_point_wkt),
            'coverage_area': WKTElement(coverage_area_wkt) if coverage_area_wkt else None
        }
        
    except Exception as e:
        print(f"❌ GIS情報計算エラー: {str(e)}")
        return {'trajectory': None, 'start_point': None, 'end_point': None, 'coverage_area': None}

@main.route('/search')
def search():
    keyword = request.args.get('q', '')
    results = MatchResult.query.filter(
        (MatchResult.team1.contains(keyword)) | (MatchResult.team2.contains(keyword))
    ).all()
    return render_template('results.html', matches=results)

@main.route('/add_tournament', methods=['GET', 'POST'])
def add_tournament():
    from .models import db, Tournament
    from datetime import datetime

    if request.method == 'POST':
        name = request.form['name']
        year = int(request.form['year'])
        start_date = datetime.strptime(request.form['start_date'], '%Y-%m-%d')
        end_date = datetime.strptime(request.form['end_date'], '%Y-%m-%d')

        tournament = Tournament(
            name=name,
            year=year,
            start_date=start_date,
            end_date=end_date
        )
        db.session.add(tournament)
        db.session.commit()
        return redirect(url_for('main.index'))

    return render_template('add_tournament.html')

@main.route('/delete_tournament/<int:tournament_id>', methods=['POST'])
def delete_tournament(tournament_id):
    from .models import db, Tournament, MatchResult
    tournament = Tournament.query.get_or_404(tournament_id)

    # 関連するMatchResultも削除（外部キー制約を考慮）
    MatchResult.query.filter_by(tournament_id=tournament.id).delete()

    db.session.delete(tournament)
    db.session.commit()
    return redirect(url_for('main.index'))

@main.route('/tournament/<int:tournament_id>')
def view_tournament(tournament_id):
    from .models import Tournament, EventData
    from sqlalchemy import func
    
    tournament = Tournament.query.get_or_404(tournament_id)
    
    # 大会に登録された試合のみを取得
    registered_matches = MatchResult.query.filter_by(tournament_id=tournament_id).all()
    
    # 登録された試合のCSVデータを確認
    matches_data = []
    for match in registered_matches:
        # RCGファイル名から対応するCSVのmatch_idを導出
        csv_match_id = match.rcg_filename.replace('.rcg.gz', '').replace('.rcg', '')
        if not csv_match_id.endswith('.event'):
            csv_match_id += '.event'
        
        # 対応するEventDataがあるかチェック
        event_count = EventData.query.filter_by(match_id=csv_match_id).count()
        
        if event_count > 0:
            matches_data.append({
                'id': match.id,  # MatchResultのID（削除用）
                'match_id': csv_match_id,
                'display_name': f"{match.team1} vs {match.team2}",
                'team1': match.team1,
                'team2': match.team2,
                'event_count': event_count,
                'date_time': match.datetime.strftime('%m/%d %H:%M') if match.datetime else 'N/A'
            })
        else:
            # CSVデータがない場合でも表示（警告付き）
            matches_data.append({
                'id': match.id,  # MatchResultのID（削除用）
                'match_id': csv_match_id,
                'display_name': f"{match.team1} vs {match.team2} (CSVなし)",
                'team1': match.team1,
                'team2': match.team2,
                'event_count': 0,
                'date_time': match.datetime.strftime('%m/%d %H:%M') if match.datetime else 'N/A'
            })
    
    # match_idでソート（他のページと統一）
    matches_data.sort(key=lambda x: x['match_id'])
    
    return render_template('tournament_detail_csv.html', 
                         tournament=tournament, 
                         matches_data=matches_data,
                         available_data_count=len(matches_data))

@main.route('/tournament/<int:tournament_id>/upload', methods=['GET', 'POST'])
def upload_csv_redirect(tournament_id):
    """CSVアップロードページへのリダイレクト"""
    from .models import Tournament
    
    tournament = Tournament.query.get_or_404(tournament_id)
    
    if request.method == 'POST':
        # CSVアップロードページへリダイレクト
        flash('💡 CSVファイルのアップロードは専用ページから行ってください。', 'info')
        return redirect(url_for('main.upload_event_csv'))
    
    # GETの場合もCSVアップロードページへリダイレクト
    return redirect(url_for('main.upload_event_csv'))



@main.route('/upload_event_csv', methods=['GET', 'POST'])
def upload_event_csv():
    if request.method == 'POST':
        db.session.expunge_all()
        db.session.rollback()
        
        files = request.files.getlist('file')  # 複数ファイル対応
        uploaded_files = []
        skipped_files = []
        total_events = 0
        
        for file in files:
            if file and file.filename.endswith('.csv'):
                filename = secure_filename(file.filename)
                match_id = os.path.splitext(filename)[0]  # ファイル名を match_id に使う
                print(f"★処理中: file.filename={file.filename}, match_id={match_id}")
                
                # 既存データのチェック
                existing_event_count = EventData.query.filter_by(match_id=match_id).count()
                existing_sequence_count = EventSequence.query.filter_by(match_id=match_id).count()
                
                if existing_event_count > 0:
                    sequence_info = f", {existing_sequence_count}シーケンス" if existing_sequence_count > 0 else ""
                    print(f"⚠️ 重複スキップ - 既にデータベースに存在: {match_id} ({existing_event_count}イベント{sequence_info})")
                    skipped_files.append({
                        'filename': filename, 
                        'events': existing_event_count, 
                        'sequences': existing_sequence_count,
                        'reason': '重複'
                    })
                    continue
                
                filepath = os.path.join('uploads', filename)
                os.makedirs('uploads', exist_ok=True)
                file.save(filepath)

                event_count = 0
                with open(filepath, newline='', encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile)
                    for row in reader:
                        event = EventData(
                            type=row.get("Type", ""),
                            side1=row.get("Side1", ""),
                            unum1=int(row["Unum1"]) if row.get("Unum1") else None,
                            time1=float(row["Time1"]) if row.get("Time1") else None,
                            mode1=row.get("Mode1") or None,
                            x1=float(row["X1"]) if row.get("X1") else 0.0,
                            y1=float(row["Y1"]) if row.get("Y1") else 0.0,
                            side2=row.get("Side2") or None,
                            unum2=int(row["Unum2"]) if row.get("Unum2") else None,
                            time2=float(row["Time2"]) if row.get("Time2") else None,
                            x2=float(row["X2"]) if row.get("X2") else None,
                            y2=float(row["Y2"]) if row.get("Y2") else None,
                            success=parse_bool(row.get("Success")),
                            match_id=match_id
                        )
                        db.session.add(event)
                        event_count += 1
                
                # イベントシーケンスの自動生成
                sequence_count = 0
                sequence_error = None
                try:
                    print(f"📊 シーケンス分析開始: {match_id}")
                    generate_event_sequences(match_id)
                    sequence_count = EventSequence.query.filter_by(match_id=match_id).count()
                    print(f"✅ シーケンス分析完了: {match_id} ({sequence_count}個のシーケンス生成)")
                except Exception as e:
                    sequence_error = str(e)
                    print(f"❌ シーケンス生成エラー ({match_id}): {e}")
                
                uploaded_files.append({
                    'filename': filename, 
                    'events': event_count,
                    'sequences': sequence_count,
                    'sequence_error': sequence_error
                })
                total_events += event_count
        
        if uploaded_files:
            try:
                db.session.commit()
                print(f"✅ {len(uploaded_files)}個のファイル、{total_events}個のイベントをデータベースに登録完了")
            except Exception as e:
                db.session.rollback()
                print(f"❌ データベースコミット時エラー: {e}")
                flash('データベースへの保存中にエラーが発生しました。管理者にお問い合わせください。', 'error')
                return redirect(url_for('main.upload_event_csv'))
        
        # 結果メッセージの作成
        if uploaded_files and skipped_files:
            total_sequences = sum(f.get('sequences', 0) for f in uploaded_files)
            sequence_errors = [f for f in uploaded_files if f.get('sequence_error')]
            
            base_msg = f'✅ {len(uploaded_files)}個のCSVファイルをアップロードしました（合計 {total_events} イベント、{total_sequences} シーケンス生成）。{len(skipped_files)}個のファイルは既存のため重複スキップされました。'
            
            if sequence_errors:
                error_files = ', '.join(f['filename'] for f in sequence_errors)
                base_msg += f' ⚠️ {len(sequence_errors)}個のファイルでシーケンス生成エラーが発生: {error_files}'
            
            flash(base_msg, 'success' if not sequence_errors else 'warning')
            
        elif uploaded_files:
            total_sequences = sum(f.get('sequences', 0) for f in uploaded_files)
            sequence_errors = [f for f in uploaded_files if f.get('sequence_error')]
            
            base_msg = f'✅ {len(uploaded_files)}個のCSVファイルを正常にアップロードしました。（合計 {total_events} イベント、{total_sequences} シーケンス生成）'
            
            if sequence_errors:
                error_files = ', '.join(f['filename'] for f in sequence_errors)
                base_msg += f' ⚠️ {len(sequence_errors)}個のファイルでシーケンス生成エラーが発生: {error_files}'
            
            flash(base_msg, 'success' if not sequence_errors else 'warning')
            
        elif skipped_files:
            flash(f'⚠️ 選択された{len(skipped_files)}個のファイルは全て既にデータベースに存在するため、アップロードされませんでした。', 'warning')
        else:
            flash('❌ アップロードするファイルが選択されていません。', 'error')
        
        # リクエストの参照元に応じてリダイレクト先を決定
        referrer = request.referrer
        if referrer and 'upload_event_csv' not in referrer:
            # トップページからのアップロードの場合はトップページに戻る
            return redirect(url_for('main.index'))
        else:
            # 直接アクセスの場合もトップページへ
            return redirect(url_for('main.index'))

    return render_template('upload_event_csv.html')

@main.route('/delete_all_events', methods=['POST'])
def delete_all_events():
    # 削除前に件数を取得
    total_count = db.session.query(EventData).count()
    db.session.query(EventData).delete()
    db.session.commit()
    
    flash(f'✅ すべてのイベントデータ（{total_count}件）を削除しました。', 'success')
    print(f"✅ EventData テーブルを全削除しました。（{total_count}件）")
    return redirect(url_for('main.manage_csv'))

@main.route('/delete_match_events', methods=['POST'])
def delete_match_events():
    """特定のmatch_idのイベントデータを削除"""
    match_id = request.form.get('match_id')
    if match_id:
        # 削除前にイベント数を取得
        event_count = db.session.query(EventData).filter_by(match_id=match_id).count()
        
        # 指定のmatch_idのイベントデータを削除
        db.session.query(EventData).filter_by(match_id=match_id).delete()
        db.session.commit()
        
        flash(f'✅ Match ID "{match_id}" のイベントデータ（{event_count}件）を削除しました。', 'success')
        print(f"✅ Match ID '{match_id}' のイベントデータ {event_count}件 を削除しました。")
    else:
        flash('❌ 削除するMatch IDが指定されていません。', 'error')
    
    return redirect(url_for('main.manage_csv'))

@main.route('/generate_all_sequences', methods=['POST'])
def generate_all_sequences():
    """全てのmatch_idに対してシーケンスを生成"""
    try:
        # 既存のシーケンスを全削除
        EventSequence.query.delete()
        db.session.commit()
        
        # 全てのmatch_idを取得
        match_ids = db.session.query(EventData.match_id).distinct().all()
        match_ids = [m[0] for m in match_ids]
        
        generated_count = 0
        for match_id in match_ids:
            print(f"🔄 シーケンス生成中: {match_id}")
            generate_event_sequences(match_id)
            generated_count += 1
        
        total_sequences = EventSequence.query.count()
        flash(f'✅ {generated_count}試合分のシーケンスを生成しました（合計 {total_sequences} シーケンス）', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'❌ シーケンス生成中にエラーが発生しました: {e}', 'error')
    
    return redirect(url_for('main.manage_csv'))

@main.route('/manage_csv')
def manage_csv():
    """CSV管理ページ"""
    start_time = time.time()
    from .models import EventData
    from sqlalchemy import func
    
    # 登録済みのmatch_idとイベント数を取得
    match_data = db.session.query(
        EventData.match_id,
        func.count(EventData.id).label('event_count')
    ).group_by(EventData.match_id).order_by(EventData.match_id).all()
    
    # 処理時間を計測
    processing_time = round(time.time() - start_time, 3)
    
    return render_template('manage_csv.html', match_data=match_data, processing_time=processing_time)

@main.route('/tournament_match_selection')
def tournament_match_selection():
    """大会と試合の選択ページ"""
    from .models import Tournament
    tournaments = Tournament.query.order_by(Tournament.year.desc(), Tournament.start_date.desc()).all()
    return render_template('tournament_match_selection.html', tournaments=tournaments)

@main.route('/api/tournaments/<int:tournament_id>/matches')
def get_tournament_matches(tournament_id):
    from .models import Tournament, EventData
    from sqlalchemy import func
    
    tournament = Tournament.query.get_or_404(tournament_id)
    
    # 大会に登録された試合のみを取得
    registered_matches = MatchResult.query.filter_by(tournament_id=tournament_id).all()
    
    matches_data = []
    for match in registered_matches:
        # RCGファイル名から対応するCSVのmatch_idを導出
        csv_match_id = match.rcg_filename.replace('.rcg.gz', '').replace('.rcg', '')
        if not csv_match_id.endswith('.event'):
            csv_match_id += '.event'
        
        # 対応するEventDataがあるかチェック
        event_count = EventData.query.filter_by(match_id=csv_match_id).count()
        
        matches_data.append({
            'id': match.id,  # MatchResultのID
            'match_id': csv_match_id,
            'display_name': f"{match.team1} vs {match.team2}",
            'team1': match.team1,
            'team2': match.team2,
            'date_time': match.datetime.strftime('%m/%d %H:%M') if match.datetime else 'N/A',
            'event_count': event_count
        })
    
    # match_idでソート（GIS検索と同じ並び順）
    matches_data.sort(key=lambda x: x['match_id'])
    
    return jsonify(matches_data)

@main.route('/api/match/<string:match_id>/sequences')
def get_match_sequences(match_id):
    """試合のイベントシーケンスを取得"""
    sequences = EventSequence.query.filter_by(match_id=match_id).order_by(
        EventSequence.team, EventSequence.sequence_number
    ).all()
    
    sequences_data = []
    for seq in sequences:
        sequences_data.append({
            'id': seq.id,
            'team': seq.team,
            'sequence_number': seq.sequence_number,
            'start_time': seq.start_time,
            'end_time': seq.end_time,
            'duration': seq.end_time - seq.start_time,
            'event_count': seq.event_count,
            'event_ids': json.loads(seq.event_ids) if seq.event_ids else []
        })
    
    return jsonify(sequences_data)

@main.route('/api/sequences/<int:sequence_id>/events')
def get_sequence_events(sequence_id):
    """シーケンスに含まれるイベント詳細を取得"""
    sequence = EventSequence.query.get_or_404(sequence_id)
    event_ids = json.loads(sequence.event_ids) if sequence.event_ids else []
    
    events = EventData.query.filter(EventData.id.in_(event_ids)).order_by(EventData.time1).all()
    
    events_data = []
    for event in events:
        events_data.append({
            'id': event.id,
            'type': event.type,
            'time1': event.time1,
            'x1': event.x1,
            'y1': event.y1,
            'x2': event.x2,
            'y2': event.y2,
            'side1': event.side1,
            'side2': event.side2,
            'unum1': event.unum1,
            'unum2': event.unum2,
            'success': event.success
        })
    
    return jsonify({
        'sequence': {
            'id': sequence.id,
            'team': sequence.team,
            'sequence_number': sequence.sequence_number,
            'start_time': sequence.start_time,
            'end_time': sequence.end_time,
            'event_count': sequence.event_count
        },
        'events': events_data
    })

@main.route('/tournament/<int:tournament_id>/add_matches', methods=['GET', 'POST'])
def add_matches_to_tournament(tournament_id):
    """大会にCSVデータから試合を追加する"""
    from .models import Tournament, EventData
    from sqlalchemy import func
    from datetime import datetime
    
    tournament = Tournament.query.get_or_404(tournament_id)
    
    if request.method == 'POST':
        # 選択された試合IDのリストを取得
        selected_match_ids = request.form.getlist('selected_matches')
        
        if not selected_match_ids:
            flash('試合が選択されていません。', 'warning')
            return redirect(url_for('main.add_matches_to_tournament', tournament_id=tournament_id))
        
        added_count = 0
        skipped_count = 0
        for csv_match_id in selected_match_ids:
            # CSVのmatch_idから基本情報を抽出
            try:
                # RCG形式のファイル名に変換（.eventを.rcgに変換）
                rcg_filename = csv_match_id.replace('.event', '.rcg')
                
                # match_idから試合情報を抽出
                parts = csv_match_id.replace('.event', '').split('-')
                if len(parts) >= 6:
                    team1_raw = parts[2]  # aeteam2024
                    team2_raw = parts[3]  # cyrus2024
                    
                    # チーム名の正規化（年数サフィックスを除去）
                    team1 = team1_raw.replace('2024', '').replace('2023', '')
                    team2 = team2_raw.replace('2024', '').replace('2023', '')
                    
                    # データベース全体で既に登録済みかチェック（一意制約対応）
                    existing_match = MatchResult.query.filter_by(rcg_filename=rcg_filename).first()
                    
                    if existing_match:
                        skipped_count += 1
                        print(f"⚠️ 一括追加 - 重複スキップ - 大会「{existing_match.tournament.name}」に既に登録済み: {rcg_filename}")
                        continue  # 既に登録済みの場合はスキップ
                    
                    # 新しい試合を作成
                    new_match = MatchResult(
                        datetime=datetime.now(),  # 仮の日時
                        team1=team1,
                        team2=team2,
                        team1_score=0,  # 仮のスコア
                        team2_score=0,  # 仮のスコア
                        rcg_filename=rcg_filename,
                        tournament_id=tournament_id
                    )
                    
                    db.session.add(new_match)
                    added_count += 1
                    
            except Exception as e:
                print(f"試合追加エラー ({csv_match_id}): {e}")
                continue
        
        try:
            if added_count > 0:
                db.session.commit()
                if skipped_count > 0:
                    flash(f'✅ {added_count}試合を大会に追加しました。（{skipped_count}試合は既に他の大会に登録済みのためスキップされました）', 'success')
                else:
                    flash(f'✅ {added_count}試合を大会に追加しました。', 'success')
            else:
                if skipped_count > 0:
                    flash(f'選択された{skipped_count}試合は全て既に他の大会に登録済みです。', 'warning')
                else:
                    flash('追加できる新しい試合がありませんでした。', 'info')
        except Exception as e:
            db.session.rollback()
            print(f"一括追加 - データベースコミット時エラー: {e}")
            flash('試合の追加中にエラーが発生しました。管理者にお問い合わせください。', 'error')
        
        return redirect(url_for('main.view_tournament', tournament_id=tournament_id))
    
    # GET: 利用可能な試合を表示
    # データベース全体で既に登録されている試合を取得
    all_registered_matches = MatchResult.query.all()
    
    # 登録済み試合のmatch_idセットを作成（より確実な比較のため）
    registered_match_ids = set()
    for match in all_registered_matches:
        # RCGファイル名からmatch_idを逆算
        csv_match_id = match.rcg_filename.replace('.rcg.gz', '').replace('.rcg', '')
        if not csv_match_id.endswith('.event'):
            csv_match_id += '.event'
        registered_match_ids.add(csv_match_id)
    
    print(f"🔍 一括追加 - データベース全体で登録済みmatch_ids: {len(registered_match_ids)}件")
    
    # CSVデータから全ての利用可能な試合を取得（match_id順でソート）
    available_matches = db.session.query(
        EventData.match_id,
        func.count(EventData.id).label('event_count')
    ).group_by(EventData.match_id).order_by(EventData.match_id).all()
    
    # 未登録の試合のみを抽出
    unregistered_matches = []
    for match_id, event_count in available_matches:
        # 既に登録済みかチェック
        if match_id not in registered_match_ids:
            # match_idから表示情報を抽出
            try:
                parts = match_id.replace('.event', '').split('-')
                if len(parts) >= 6:
                    date_part = parts[0]  # 0803
                    time_part = parts[1]  # 0011
                    team1 = parts[2].replace('2024', '').replace('2023', '')
                    team2 = parts[3].replace('2024', '').replace('2023', '')
                    match_num = parts[4]  # 0001
                    sim_part = parts[5]   # sim01
                    
                    unregistered_matches.append({
                        'match_id': match_id,
                        'display_name': f"{team1} vs {team2} ({match_num}-{sim_part})",
                        'team1': team1,
                        'team2': team2,
                        'event_count': event_count,
                        'date_time': f"{date_part}-{time_part}"
                    })
            except Exception:
                # パース失敗時はそのまま表示
                unregistered_matches.append({
                    'match_id': match_id,
                    'display_name': match_id,
                    'team1': 'Unknown',
                    'team2': 'Unknown',
                    'event_count': event_count,
                    'date_time': 'Unknown'
                })
    
    # match_idでソート（GIS検索と同じ並び順）
    unregistered_matches.sort(key=lambda x: x['match_id'])
    
    print(f"✅ 全試合数: {len(available_matches)}, 登録済み: {len(registered_match_ids)}, 未登録: {len(unregistered_matches)}")
    
    return render_template('add_matches_to_tournament.html',
                         tournament=tournament,
                         unregistered_matches=unregistered_matches,
                         registered_count=len(registered_match_ids))

@main.route("/event_sequence_filtered")
def event_sequence_filtered():
    """EventSequenceテーブルを活用したシーケンス分析機能"""
    from sqlalchemy import and_, or_
    import json
    
    start_time = time.time()

    # パラメータ取得
    selected_type = request.args.get("event_type")
    selected_team = request.args.get("team")
    selected_match = request.args.get("match_id")
    selected_unum = request.args.get("unum")
    selected_success = request.args.get("success")
    selected_mode = request.args.get("mode")
    x_min = request.args.get("x_min", type=float)
    x_max = request.args.get("x_max", type=float)
    y_min = request.args.get("y_min", type=float)
    y_max = request.args.get("y_max", type=float)
    rects_json = request.args.get("rects_json")

    # 矩形リスト取得
    rects = []
    if rects_json:
        try:
            rects = json.loads(rects_json)
        except Exception:
            rects = []

    def is_in_any_rect(event):
        """イベントが指定された矩形のいずれかに含まれるかチェック"""
        for rect in rects:
            if (rect["x_min"] <= event.x1 <= rect["x_max"] and
                rect["y_min"] <= event.y1 <= rect["y_max"]):
                return True
        return False

    def sequence_matches_filter(event_ids, filters):
        """シーケンスが指定されたフィルターに一致するかチェック"""
        if not event_ids:
            return False
        
        # イベントIDリストから実際のイベントを取得
        events = EventData.query.filter(EventData.id.in_(event_ids)).order_by(EventData.time1).all()
        
        for event in events:
            # 基本フィルター条件をチェック
            if filters.get('type') and event.type != filters['type']:
                continue
            if filters.get('team') and event.side1 != filters['team']:
                continue
            if filters.get('match_id') and event.match_id != filters['match_id']:
                continue
            if filters.get('unum') and event.unum1 != int(filters['unum']):
                continue
            if filters.get('success') is not None and event.success != filters['success']:
                continue
            if filters.get('mode') and event.mode1 != filters['mode']:
                continue
            
            # 座標フィルター
            x_min, x_max = filters.get('x_min'), filters.get('x_max')
            if None not in (x_min, x_max) and not (x_min <= event.x1 <= x_max):
                continue
            
            y_min, y_max = filters.get('y_min'), filters.get('y_max')
            if None not in (y_min, y_max) and not (y_min <= event.y1 <= y_max):
                continue
            
            # 矩形フィルター
            if rects and not is_in_any_rect(event):
                continue
            
            # 一つでも条件に一致するイベントがあればシーケンス全体を含める
            return True
        
        return False

    # EventSequenceテーブルから基本的なフィルター条件に基づいてシーケンスを取得
    sequence_query = EventSequence.query
    
    # 試合指定があれば絞り込み
    if selected_match:
        # イベントIDを解析して該当する試合のシーケンスを絞り込み
        sequence_query = sequence_query.filter(
            EventSequence.event_ids.contains(f'"{selected_match}"')
        )
    
    # チーム指定があれば絞り込み
    if selected_team:
        sequence_query = sequence_query.filter(EventSequence.team == selected_team)

    sequences = sequence_query.order_by(EventSequence.start_time).all()
    
    # EventSequenceテーブルが空の場合の処理
    if not sequences:
        # 古い実装にフォールバック（動的生成）
        return event_sequence_filtered_fallback()

    # 詳細フィルター適用
    filters = {
        'type': selected_type,
        'team': selected_team,
        'match_id': selected_match,
        'unum': selected_unum,
        'success': selected_success == "true" if selected_success else None,
        'mode': selected_mode,
        'x_min': x_min,
        'x_max': x_max,
        'y_min': y_min,
        'y_max': y_max
    }

    # フィルターを通すシーケンスを特定
    matching_sequences = []
    for sequence in sequences:
        try:
            event_ids = json.loads(sequence.event_ids)
            if sequence_matches_filter(event_ids, filters):
                matching_sequences.append(sequence)
        except (json.JSONDecodeError, TypeError):
            continue

    # シーケンス詳細データを構築
    sequences_data = []
    for sequence in matching_sequences:
        try:
            event_ids = json.loads(sequence.event_ids)
            events = EventData.query.filter(EventData.id.in_(event_ids)).order_by(EventData.time1).all()
            
            sequence_events = []
            for event in events:
                sequence_events.append({
                    "id": event.id,
                    "match_id": event.match_id,
                    "time1": event.time1,
                    "time2": event.time2,
                    "type": event.type,
                    "side1": event.side1,
                    "side2": event.side2,
                    "unum1": event.unum1,
                    "unum2": event.unum2,
                    "x1": event.x1,
                    "y1": event.y1,
                    "x2": event.x2,
                    "y2": event.y2,
                    "mode1": event.mode1,
                    "success": event.success,
                })
            
            sequences_data.append(sequence_events)
            
        except (json.JSONDecodeError, TypeError):
            continue

    processing_time = round(time.time() - start_time, 3)
    print(f"⚡ EventSequence活用版 - 処理時間: {processing_time}秒, マッチング: {len(sequences_data)}シーケンス")

    return render_template("event_sequence.html",
                           sequences=sequences_data,
                           team=selected_team or "不明",
                           selected_type=selected_type,
                           selected_match=selected_match,
                           selected_unum=selected_unum,
                           selected_success=selected_success,
                           selected_mode=selected_mode,
                           x_min=x_min, x_max=x_max,
                           y_min=y_min, y_max=y_max,
                           rects_json=rects_json,
                           processing_time=processing_time)


def event_sequence_filtered_fallback():
    """EventSequenceテーブルが空の場合の従来実装（フォールバック）"""
    from sqlalchemy import or_, and_
    import json

    selected_type = request.args.get("event_type")
    selected_team = request.args.get("team")
    selected_match = request.args.get("match_id")
    selected_unum = request.args.get("unum")
    selected_success = request.args.get("success")
    selected_mode = request.args.get("mode")
    x_min = request.args.get("x_min", type=float)
    x_max = request.args.get("x_max", type=float)
    y_min = request.args.get("y_min", type=float)
    y_max = request.args.get("y_max", type=float)
    rects_json = request.args.get("rects_json")

    query = EventData.query

    # フィルター適用
    if selected_type:
        query = query.filter(EventData.type == selected_type)
    if selected_team:
        query = query.filter(EventData.side1 == selected_team)
    if selected_match:
        query = query.filter(EventData.match_id == selected_match)
    if selected_unum:
        query = query.filter(EventData.unum1 == int(selected_unum))
    if selected_success:
        query = query.filter(EventData.success == (selected_success == "true"))
    if selected_mode:
        query = query.filter(EventData.mode1 == selected_mode)
    if None not in (x_min, x_max):
        query = query.filter(EventData.x1 >= x_min, EventData.x1 <= x_max)
    if None not in (y_min, y_max):
        query = query.filter(EventData.y1 >= y_min, EventData.y1 <= y_max)

    # 矩形リスト取得
    rects = []
    if rects_json:
        try:
            rects = json.loads(rects_json)
        except Exception:
            rects = []

    def is_in_any_rect(event):
        for rect in rects:
            if (
                rect["x_min"] <= event.x1 <= rect["x_max"] and
                rect["y_min"] <= event.y1 <= rect["y_max"]
            ):
                return True
        return False

    # フィルタリングされたイベントを取得
    filtered_events = query.order_by(EventData.time1).all()
    
    # 全イベント（フィルタなし）も取得して間にある他チームのイベントを検出
    all_events_query = EventData.query
    if selected_match:
        all_events_query = all_events_query.filter(EventData.match_id == selected_match)
    all_events = all_events_query.order_by(EventData.time1).all()

    def has_interrupting_events(start_time, end_time, target_team):
        """指定された時間範囲内に対象チーム以外のイベントがあるかチェック"""
        for event in all_events:
            if (start_time < event.time1 < end_time and 
                event.side1 and event.side1 != target_team):
                return True
        return False

    # 連続イベント抽出（従来ロジック）
    sequences = []
    current_sequence = []
    current_team = None
    tracking = False
    last_mode = None

    def end_current_sequence():
        nonlocal current_sequence, current_team, tracking
        if current_sequence:
            sequences.append(current_sequence)
            current_sequence = []
        current_team = None
        tracking = False

    for i, event in enumerate(filtered_events):
        # 不自然なInterception除外
        is_reset = (
            event.type == "Interception" and
            event.mode1 == "play_on" and
            event.x2 == 0.0 and event.y2 == 0.0
        )

        if is_reset:
            if tracking and current_sequence:
                end_current_sequence()
            last_mode = event.mode1
            continue

        if not tracking:
            if rects and not is_in_any_rect(event):
                continue

            if event.side2 == "neutral" or (event.side2 and event.side2 != event.side1):
                sequences.append([event])
                last_mode = event.mode1
                continue

            current_sequence = [event]
            current_team = event.side1
            tracking = True
            last_mode = event.mode1
            continue
        
        if (tracking and current_sequence and 
            has_interrupting_events(current_sequence[-1].time1, event.time1, current_team)):
            end_current_sequence()
            if rects and not is_in_any_rect(event):
                last_mode = event.mode1
                continue
            if event.side2 == "neutral" or (event.side2 and event.side2 != event.side1):
                sequences.append([event])
                last_mode = event.mode1
                continue
            current_sequence = [event]
            current_team = event.side1
            tracking = True
            last_mode = event.mode1
            continue
        
        if event.side1 and event.side1 != current_team:
            end_current_sequence()
            if rects and not is_in_any_rect(event):
                last_mode = event.mode1
                continue
            if event.side2 == "neutral" or (event.side2 and event.side2 != event.side1):
                sequences.append([event])
                last_mode = event.mode1
                continue
            current_sequence = [event]
            current_team = event.side1
            tracking = True
            last_mode = event.mode1
            continue

        if (event.side2 == "neutral" or 
            (event.side2 and event.side2 != current_team) or 
            (event.side1 and event.side1 != current_team)):
            current_sequence.append(event)
            end_current_sequence()
            last_mode = event.mode1
            continue

        current_sequence.append(event)
        last_mode = event.mode1

    if tracking and current_sequence:
        sequences.append(current_sequence)

    # シリアライズ
    def serialize_event(event):
        return {
            "id": event.id,
            "match_id": event.match_id,
            "time1": event.time1,
            "time2": event.time2,
            "type": event.type,
            "side1": event.side1,
            "side2": event.side2,
            "unum1": event.unum1,
            "unum2": event.unum2,
            "x1": event.x1,
            "y1": event.y1,
            "x2": event.x2,
            "y2": event.y2,
            "mode1": event.mode1,
            "success": event.success,
        }

    sequences_serialized = [[serialize_event(e) for e in seq] for seq in sequences]

    return render_template("event_sequence.html",
                           sequences=sequences_serialized,
                           team=selected_team or "不明",
                           selected_type=selected_type,
                           selected_match=selected_match,
                           selected_unum=selected_unum,
                           selected_success=selected_success,
                           selected_mode=selected_mode,
                           x_min=x_min, x_max=x_max,
                           y_min=y_min, y_max=y_max,
                           rects_json=rects_json,
                           fallback_mode=True)

@main.route('/interactive_gis_map')
def interactive_gis_map():
    """インタラクティブGISマップページ"""
    start_time = time.time()
    print("🔍 interactive_gis_map called")
    
    # イベントフィルタリング機能
    selected_type = request.args.get('event_type')
    selected_team = request.args.get('team')
    selected_unum = request.args.get('unum')
    selected_success = request.args.get('success')
    selected_match = request.args.get("match_id", '')
    
    # match_idは既に.event形式でデータベースに格納されている
    if selected_match:
        print(f"🔍 Selected match ID: {selected_match}")
    
    selected_mode = request.args.get('mode')
    x_min = request.args.get('x_min')
    x_max = request.args.get('x_max')
    y_min = request.args.get('y_min')
    y_max = request.args.get('y_max')
    
    # 時間フィルタのパラメータを取得（両方のフィールドセットをサポート）
    min_time = request.args.get("min_time")
    max_time = request.args.get("max_time") 
    min_time1 = request.args.get("min_time1")
    max_time1 = request.args.get("max_time1")
    
    # どちらのフィールドセットが使用されているかを判断（min_timeを優先）
    effective_min_time = min_time if min_time else min_time1
    effective_max_time = max_time if max_time else max_time1
    
    match_ids = [m[0] for m in db.session.query(EventData.match_id).distinct().order_by(EventData.match_id).all()]
    page = request.args.get("page", 1, type=int)
    per_page = 50  # イベントリストのページネーション用
    
    # Display Limitパラメータを取得（フロントエンドで制御するが、バックエンドでも制限を設ける）
    limit = request.args.get('limit', 1000, type=int)
    map_limit = min(limit, 5000)  # 最大5000イベントまで制限（マップ表示用）

    query = EventData.query

    # フィルタ処理
    if selected_type:
        query = query.filter(EventData.type == selected_type)
    if selected_team:
        query = query.filter(EventData.side1 == selected_team)
    if selected_mode:
        query = query.filter(EventData.mode1 == selected_mode)
    if selected_unum:
        try:
            query = query.filter(EventData.unum1 == int(selected_unum))
        except ValueError:
            pass
    if selected_success in ["true", "false"]:
        query = query.filter(EventData.success == (selected_success == "true"))
    if selected_match and selected_match != "all":
        query = query.filter(EventData.match_id == selected_match)
    if x_min:
        try:
            query = query.filter(EventData.x1 >= float(x_min))
        except ValueError:
            pass
    if x_max:
        try:
            query = query.filter(EventData.x1 <= float(x_max))
        except ValueError:
            pass
    if y_min:
        try:
            query = query.filter(EventData.y1 >= float(y_min))
        except ValueError:
            pass
    if y_max:
        try:
            query = query.filter(EventData.y1 <= float(y_max))
        except ValueError:
            pass
    if effective_min_time:
        try:
            query = query.filter(EventData.time1 >= float(effective_min_time))
            print(f"⏰ Applied min time filter: {effective_min_time}")
        except ValueError:
            pass
    if effective_max_time:
        try:
            query = query.filter(EventData.time1 <= float(effective_max_time))
            print(f"⏰ Applied max time filter: {effective_max_time}")
        except ValueError:
            pass

    # 空間検索処理（図形による範囲検索）
    shape_type = request.args.get('shape_type')
    shape_data = request.args.get('shape_data')
    
    # 座標による矩形範囲検索パラメータ
    rect_xmin = request.args.get('rect_xmin')
    rect_ymin = request.args.get('rect_ymin')
    rect_xmax = request.args.get('rect_xmax')
    rect_ymax = request.args.get('rect_ymax')
    
    # 座標パラメータがすべて指定されている場合の処理
    if rect_xmin and rect_ymin and rect_xmax and rect_ymax:
        try:
            xmin = float(rect_xmin)
            ymin = float(rect_ymin)
            xmax = float(rect_xmax)
            ymax = float(rect_ymax)
            
            # 範囲チェック
            if xmin < xmax and ymin < ymax:
                print(f"📐 Coordinate search: X({xmin}~{xmax}), Y({ymin}~{ymax})")
                query = query.filter(
                    EventData.x1 >= xmin,
                    EventData.x1 <= xmax,
                    EventData.y1 >= ymin,
                    EventData.y1 <= ymax
                )
            else:
                print(f"❌ Invalid coordinate range: X({xmin}~{xmax}), Y({ymin}~{ymax})")
                
        except (ValueError, TypeError) as e:
            print(f"❌ Error parsing coordinate parameters: {e}")
    
    if (shape_type and shape_data):
        try:
            print(f"🎯 Spatial search: type={shape_type}")
            shape_info = json.loads(shape_data)
            
            if shape_type == 'circle':
                # 円の範囲検索
                center_lng = shape_info['center']['x']
                center_lat = shape_info['center']['y']
                radius = shape_info['radius']
                
                # 座標変換: フロントエンドのマップ座標 → RoboCup座標
                robocup_x = center_lng - 52.5
                robocup_y = center_lat - 34
                
                # SQL条件で円の範囲をフィルタ
                distance_condition = func.sqrt(
                    func.pow(EventData.x1 - robocup_x, 2) + 
                    func.pow(EventData.y1 - robocup_y, 2)
                ) <= radius
                
                query = query.filter(distance_condition)
                
            elif shape_type == 'rectangle':
                # 矩形の範囲検索
                bounds = shape_info['bounds']
                
                # 座標変換: マップ座標 → RoboCup座標
                robocup_x_west = bounds['west'] - 52.5
                robocup_x_east = bounds['east'] - 52.5
                robocup_y_south = bounds['south'] - 34
                robocup_y_north = bounds['north'] - 34
                
                query = query.filter(
                    EventData.x1 >= robocup_x_west,
                    EventData.x1 <= robocup_x_east,
                    EventData.y1 >= robocup_y_south,
                    EventData.y1 <= robocup_y_north
                )
                
            elif shape_type == 'polygon':
                # 多角形の範囲検索（簡易実装：境界ボックス内に限定）
                coordinates = shape_info['coordinates']
                if coordinates:
                    map_lng_coords = [coord['x'] for coord in coordinates]
                    map_lat_coords = [coord['y'] for coord in coordinates]
                    
                    robocup_x_coords = [lng - 52.5 for lng in map_lng_coords]
                    robocup_y_coords = [lat - 34 for lat in map_lat_coords]
                    
                    min_x, max_x = min(robocup_x_coords), max(robocup_x_coords)
                    min_y, max_y = min(robocup_y_coords), max(robocup_y_coords)
                    
                    query = query.filter(
                        EventData.x1 >= min_x,
                        EventData.x1 <= max_x,
                        EventData.y1 >= min_y,
                        EventData.y1 <= max_y
                    )
                    
            elif shape_type == 'multiple':
                # 複数図形の処理
                shape_filters = []
                for i, shape in enumerate(shape_info):
                    shape_filter_conditions = []
                    
                    if shape['type'] == 'circle':
                        center_lng = shape['data']['center']['x']
                        center_lat = shape['data']['center']['y']
                        radius = shape['data']['radius']
                        robocup_center_x = center_lng - 52.5
                        robocup_center_y = center_lat - 34
                        
                        shape_filter_conditions.append(
                            func.sqrt(
                                func.pow(EventData.x1 - robocup_center_x, 2) + 
                                func.pow(EventData.y1 - robocup_center_y, 2)
                            ) <= radius
                        )
                    elif shape['type'] == 'rectangle':
                        bounds = shape['data']['bounds']
                        robocup_x_west = bounds['west'] - 52.5
                        robocup_x_east = bounds['east'] - 52.5
                        robocup_y_south = bounds['south'] - 34
                        robocup_y_north = bounds['north'] - 34
                        
                        shape_filter_conditions.append(and_(
                            EventData.x1 >= robocup_x_west,
                            EventData.x1 <= robocup_x_east,
                            EventData.y1 >= robocup_y_south,
                            EventData.y1 <= robocup_y_north
                        ))
                    elif shape['type'] == 'polygon':
                        coordinates = shape['data']['coordinates']
                        if coordinates:
                            map_lng_coords = [coord['x'] for coord in coordinates]
                            map_lat_coords = [coord['y'] for coord in coordinates]
                            robocup_x_coords = [lng - 52.5 for lng in map_lng_coords]
                            robocup_y_coords = [lat - 34 for lat in map_lat_coords]
                            min_x, max_x = min(robocup_x_coords), max(robocup_x_coords)
                            min_y, max_y = min(robocup_y_coords), max(robocup_y_coords)
                            
                            shape_filter_conditions.append(and_(
                                EventData.x1 >= min_x,
                                EventData.x1 <= max_x,
                                EventData.y1 >= min_y,
                                EventData.y1 <= max_y
                            ))
                    
                    if shape_filter_conditions:
                        shape_filters.extend(shape_filter_conditions)
                
                if shape_filters:
                    query = query.filter(or_(*shape_filters))
                    
        except Exception as e:
            print(f"空間検索の処理エラー: {e}")

    # シーケンスフィルタ処理
    sequence_ids = request.args.get('sequence_ids')
    if sequence_ids:
        try:
            # カンマ区切りのIDを配列に変換
            sequence_id_list = [int(id.strip()) for id in sequence_ids.split(',') if id.strip()]
            if sequence_id_list:
                query = query.filter(EventData.id.in_(sequence_id_list))
        except Exception as e:
            print(f"シーケンスフィルタの処理エラー: {e}")

    # 複数矩形範囲処理（下位互換として保持）
    rects_json = request.args.get("rects_json")
    if rects_json:
        try:
            rects = json.loads(rects_json)
            rect_filters = []
            for r in rects:
                rect_filters.append(and_(
                    EventData.x1 >= r['x_min'],
                    EventData.x1 <= r['x_max'],
                    EventData.y1 >= r['y_min'],
                    EventData.y1 <= r['y_max']
                ))
            if rect_filters:
                query = query.filter(or_(*rect_filters))
        except Exception as e:
            print("矩形フィルタの処理エラー:", e)

    # イベントデータ取得（2つのデータセットを作成）
    # 1. マップ表示用（制限あり）
    map_events = query.order_by(EventData.match_id, EventData.time1).limit(map_limit).all()
    
    # 2. ページネーション用（Event List用）
    import math
    total_items = query.count()
    total_pages = math.ceil(total_items / per_page)
    
    # ページネーション実装
    offset = (page - 1) * per_page
    list_events = query.order_by(EventData.match_id, EventData.time1).offset(offset).limit(per_page).all()
    
    # フィルタ選択肢のデータ取得
    event_types = [et[0] for et in db.session.query(EventData.type).distinct().order_by(EventData.type)]
    teams = [t[0] for t in db.session.query(EventData.side1).distinct().order_by(EventData.side1)]
    modes = [m[0] for m in db.session.query(EventData.mode1).distinct().order_by(EventData.mode1)]
    unums = [u[0] for u in db.session.query(EventData.unum1).distinct().order_by(EventData.unum1) if u[0] is not None]

    # EventSequenceデータも取得（GIS情報付き）
    sequence_query = EventSequence.query
    if selected_match and selected_match != "all":
        sequence_query = sequence_query.filter(EventSequence.match_id == selected_match)
    if selected_team:
        sequence_query = sequence_query.filter(EventSequence.team == selected_team)
    
    sequences = sequence_query.limit(100).all()  # 表示制限
    
    # シーケンスデータをJSONシリアライズ可能な形式に変換
    sequences_data = []
    for seq in sequences:
        seq_data = {
            'id': seq.id,
            'team': seq.team,
            'sequence_number': seq.sequence_number,
            'start_time': seq.start_time,
            'end_time': seq.end_time,
            'event_count': seq.event_count,
            'trajectory_wkt': seq.trajectory_wkt,
            'start_point_wkt': seq.start_point_wkt,
            'end_point_wkt': seq.end_point_wkt,
            'coverage_area_wkt': seq.coverage_area_wkt
        }
        sequences_data.append(seq_data)

    # 処理時間を計測（データ取得後）
    processing_time = round(time.time() - start_time, 3)

    return render_template('interactive_gis_map.html',
                           events=map_events,  # マップ表示用データ
                           list_events=list_events,  # イベントリスト表示用データ
                           sequences=sequences_data,  # EventSequenceデータ（GIS情報付き）
                           event_types=event_types,
                           teams=teams,
                           modes=modes,
                           unums=unums,
                           match_ids=match_ids,
                           total_events=total_items,
                           current_page=page,
                           total_pages=total_pages,
                           per_page=per_page,
                           has_spatial_filter=bool(shape_type and shape_data),
                           selected_type=request.args.get("event_type", ""),
                           selected_team=request.args.get("team", ""),
                           selected_unum=request.args.get("unum", ""),
                           selected_success=request.args.get("success", ""),
                           selected_mode=request.args.get("mode", ""),
                           selected_match=request.args.get("match_id", ""),
                           x_min=request.args.get("x_min", ""),
                           x_max=request.args.get("x_max", ""),
                           y_min=request.args.get("y_min", ""),
                           y_max=request.args.get("y_max", ""),
                           selected_min_time=effective_min_time or "",
                           selected_max_time=effective_max_time or "",
                           min_time1=request.args.get("min_time1", ""),
                           max_time1=request.args.get("max_time1", ""),
                           shape_type=shape_type or "",
                           shape_data=shape_data or "",
                           sequence_ids=request.args.get("sequence_ids", ""),
                           rects_json=request.args.get("rects_json", ""),
                           processing_time=processing_time)

@main.route('/download_search_results_csv')
def download_search_results_csv():
    """検索結果をCSVファイルとしてダウンロード"""
    from datetime import datetime
    start_time = time.time()
    
    # interactive_gis_map関数と同じフィルタロジックを使用してクエリを構築
    query = db.session.query(EventData)
    
    # 基本フィルタ処理
    event_type = request.args.get("event_type")
    if event_type:
        query = query.filter(EventData.type == event_type)
    
    team = request.args.get("team")
    if team:
        query = query.filter(EventData.side1 == team)
    
    unum = request.args.get("unum")
    if unum:
        query = query.filter(EventData.unum1 == unum)
    
    match_id = request.args.get("match_id")
    if match_id:
        query = query.filter(EventData.match_id == match_id)
    
    success = request.args.get("success")
    if success:
        if success == "true":
            query = query.filter(EventData.success == True)
        elif success == "false":
            query = query.filter(EventData.success == False)
    
    # 時間範囲フィルタ
    min_time = request.args.get("min_time")
    if min_time:
        try:
            min_time_val = float(min_time)
            query = query.filter(EventData.time1 >= min_time_val)
        except ValueError:
            pass
    
    max_time = request.args.get("max_time")
    if max_time:
        try:
            max_time_val = float(max_time)
            query = query.filter(EventData.time1 <= max_time_val)
        except ValueError:
            pass
    
    # 座標範囲フィルタ（矩形範囲）
    rect_xmin = request.args.get("rect_xmin")
    rect_xmax = request.args.get("rect_xmax")
    rect_ymin = request.args.get("rect_ymin")
    rect_ymax = request.args.get("rect_ymax")
    
    if rect_xmin and rect_xmax and rect_ymin and rect_ymax:
        try:
            query = query.filter(
                EventData.x1 >= float(rect_xmin),
                EventData.x1 <= float(rect_xmax),
                EventData.y1 >= float(rect_ymin),
                EventData.y1 <= float(rect_ymax)
            )
        except ValueError:
            pass
    
    # 空間検索フィルタ（図形描画）
    shape_type = request.args.get("shape_type")
    shape_data = request.args.get("shape_data")
    
    if shape_type and shape_data:
        try:
            shape_info = json.loads(shape_data)
            
            if shape_type == 'circle':
                # 円の範囲検索
                center_lng = shape_info['center']['x']
                center_lat = shape_info['center']['y']
                radius = shape_info['radius']
                
                # 座標変換: フロントエンドのマップ座標 → RoboCup座標
                robocup_x = center_lng - 52.5
                robocup_y = center_lat - 34
                
                # SQL条件で円の範囲をフィルタ
                distance_condition = func.sqrt(
                    func.pow(EventData.x1 - robocup_x, 2) + 
                    func.pow(EventData.y1 - robocup_y, 2)
                ) <= radius
                
                query = query.filter(distance_condition)
                
            elif shape_type == 'rectangle':
                # 矩形の範囲検索
                bounds = shape_info['bounds']
                
                # 座標変換: マップ座標 → RoboCup座標
                robocup_x_west = bounds['west'] - 52.5
                robocup_x_east = bounds['east'] - 52.5
                robocup_y_south = bounds['south'] - 34
                robocup_y_north = bounds['north'] - 34
                
                query = query.filter(
                    EventData.x1 >= robocup_x_west,
                    EventData.x1 <= robocup_x_east,
                    EventData.y1 >= robocup_y_south,
                    EventData.y1 <= robocup_y_north
                )
                
            elif shape_type == 'polygon':
                # 多角形の範囲検索（簡易実装：境界ボックス内に限定）
                coordinates = shape_info['coordinates']
                if coordinates:
                    map_lng_coords = [coord['x'] for coord in coordinates]
                    map_lat_coords = [coord['y'] for coord in coordinates]
                    
                    robocup_x_coords = [lng - 52.5 for lng in map_lng_coords]
                    robocup_y_coords = [lat - 34 for lat in map_lat_coords]
                    
                    min_x, max_x = min(robocup_x_coords), max(robocup_x_coords)
                    min_y, max_y = min(robocup_y_coords), max(robocup_y_coords)
                    
                    query = query.filter(
                        EventData.x1 >= min_x,
                        EventData.x1 <= max_x,
                        EventData.y1 >= min_y,
                        EventData.y1 <= max_y
                    )
                    
            elif shape_type == 'multiple':
                # 複数図形の処理
                if isinstance(shape_info, list):
                    shape_filters = []
                    for shape in shape_info:
                        shape_filter_conditions = []
                        
                        if shape['type'] == 'circle':
                            center = shape['data']['center']
                            radius = shape['data']['radius']
                            
                            robocup_center_x = center['x'] - 52.5
                            robocup_center_y = center['y'] - 34
                            
                            shape_filter_conditions.append(
                                func.sqrt(
                                    func.power(EventData.x1 - robocup_center_x, 2) +
                                    func.power(EventData.y1 - robocup_center_y, 2)
                                ) <= radius
                            )
                        elif shape['type'] == 'rectangle':
                            bounds = shape['data']['bounds']
                            robocup_x_west = bounds['west'] - 52.5
                            robocup_x_east = bounds['east'] - 52.5
                            robocup_y_south = bounds['south'] - 34
                            robocup_y_north = bounds['north'] - 34
                            
                            shape_filter_conditions.append(and_(
                                EventData.x1 >= robocup_x_west,
                                EventData.x1 <= robocup_x_east,
                                EventData.y1 >= robocup_y_south,
                                EventData.y1 <= robocup_y_north
                            ))
                        elif shape['type'] == 'polygon':
                            coordinates = shape['data']['coordinates']
                            if coordinates:
                                map_lng_coords = [coord['x'] for coord in coordinates]
                                map_lat_coords = [coord['y'] for coord in coordinates]
                                robocup_x_coords = [lng - 52.5 for lng in map_lng_coords]
                                robocup_y_coords = [lat - 34 for lat in map_lat_coords]
                                min_x, max_x = min(robocup_x_coords), max(robocup_x_coords)
                                min_y, max_y = min(robocup_y_coords), max(robocup_y_coords)
                                
                                shape_filter_conditions.append(and_(
                                    EventData.x1 >= min_x,
                                    EventData.x1 <= max_x,
                                    EventData.y1 >= min_y,
                                    EventData.y1 <= max_y
                                ))
                        
                        if shape_filter_conditions:
                            shape_filters.extend(shape_filter_conditions)
                    
                    if shape_filters:
                        query = query.filter(or_(*shape_filters))
                    
        except Exception as e:
            print(f"空間検索の処理エラー: {e}")

    # シーケンスフィルタ処理
    sequence_ids = request.args.get('sequence_ids')
    if sequence_ids:
        try:
            sequence_id_list = [int(id.strip()) for id in sequence_ids.split(',') if id.strip()]
            if sequence_id_list:
                query = query.filter(EventData.id.in_(sequence_id_list))
        except Exception as e:
            print(f"シーケンスフィルタの処理エラー: {e}")

    # 複数矩形範囲処理（下位互換として保持）
    rects_json = request.args.get("rects_json")
    if rects_json:
        try:
            rects = json.loads(rects_json)
            rect_filters = []
            for r in rects:
                rect_filters.append(and_(
                    EventData.x1 >= r['x_min'],
                    EventData.x1 <= r['x_max'],
                    EventData.y1 >= r['y_min'],
                    EventData.y1 <= r['y_max']
                ))
            if rect_filters:
                query = query.filter(or_(*rect_filters))
        except Exception as e:
            print("矩形フィルタの処理エラー:", e)

    # データを取得（制限なし - 全件取得）
    events = query.order_by(EventData.match_id, EventData.time1).all()
    
    # CSVファイルを作成
    output = StringIO()
    writer = csv.writer(output)
    
    # CSVヘッダー
    headers = [
        'Type1', 'Side1', 'Unum1', 'Time1', 'Mode1', 'X1', 'Y1', 
        'Side2', 'Unum2', 'Time2', 'X2', 'Y2', 'Success', 'match_id'
    ]
    writer.writerow(headers)
    
    # データ行
    for event in events:
        writer.writerow([
            event.type,
            event.side1,
            event.unum1 if event.unum1 is not None else '',
            event.time1,
            event.mode1 if event.mode1 else '',
            event.x1,
            event.y1,
            event.side2 if event.side2 else '',
            event.unum2 if event.unum2 is not None else '',
            event.time2 if event.time2 is not None else '',
            event.x2 if event.x2 is not None else '',
            event.y2 if event.y2 is not None else '',
            event.success if event.success is not None else '',
            event.match_id
        ])
    
    # ファイル名を生成（日時とフィルタ情報を含む）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename_parts = [f"search_results_{timestamp}"]
    
    # フィルタ情報をファイル名に追加
    if event_type:
        filename_parts.append(f"type_{event_type}")
    if team:
        filename_parts.append(f"team_{team}")
    if match_id:
        filename_parts.append(f"match_{match_id}")
    if min_time or max_time:
        time_info = f"time_{min_time or '0'}-{max_time or 'inf'}"
        filename_parts.append(time_info)
    if shape_type:
        filename_parts.append(f"spatial_{shape_type}")
    if sequence_ids:
        filename_parts.append("sequence_filtered")
    
    filename = "_".join(filename_parts) + ".csv"
    filename = filename.replace(" ", "_")  # スペースをアンダースコアに置換
    
    # レスポンスを作成
    output.seek(0)
    processing_time = round(time.time() - start_time, 3)
    
    response = Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )
    
    print(f"📄 CSV exported: {len(events)} events in {processing_time}s -> {filename}")
    return response



@main.route('/tournament/<int:tournament_id>/add_match', methods=['GET', 'POST'])
def add_match_to_tournament(tournament_id):
    """大会に試合を追加する機能"""
    from .models import Tournament, EventData
    from datetime import datetime
    from sqlalchemy import func
    
    tournament = Tournament.query.get_or_404(tournament_id)
    
    if request.method == 'POST':
        selected_match_ids = request.form.getlist('selected_matches')
        
        if not selected_match_ids:
            flash('試合を選択してください。', 'warning')
            return redirect(url_for('main.add_match_to_tournament', tournament_id=tournament_id))
        
        added_count = 0
        skipped_count = 0
        for csv_match_id in selected_match_ids:
            # CSVのmatch_idからRCGファイル名を逆算
            rcg_filename = csv_match_id.replace('.event', '.rcg')
            
            # データベース全体で既に登録済みかチェック（一意制約対応）
            existing_match = MatchResult.query.filter_by(rcg_filename=rcg_filename).first()
            
            if existing_match:
                skipped_count += 1
                print(f"⚠️ 重複スキップ - 大会「{existing_match.tournament.name}」に既に登録済み: {rcg_filename}")
                continue  # 既に登録済みの場合はスキップ
            
            # match_idから試合情報を抽出
            try:
                parts = csv_match_id.replace('.event', '').split('-')
                if len(parts) >= 6:
                    team1 = parts[2]  # 2024を保持
                    team2 = parts[3]  # 2024を保持
                    
                    # 新しい試合を作成
                    new_match = MatchResult(
                        datetime=datetime.now(),
                        team1=team1,
                        team2=team2,
                        team1_score=0,  # デフォルト値
                        team2_score=0,  # デフォルト値
                        rcg_filename=rcg_filename,
                        tournament_id=tournament_id
                    )
                    
                    db.session.add(new_match)
                    added_count += 1
                    
            except Exception as e:
                print(f"試合追加エラー: {e}")
                continue
        
        try:
            if added_count > 0:
                db.session.commit()
                if skipped_count > 0:
                    flash(f'✅ {added_count}試合を大会に追加しました。（{skipped_count}試合は既に他の大会に登録済みのためスキップされました）', 'success')
                else:
                    flash(f'✅ {added_count}試合を大会に追加しました。', 'success')
            else:
                if skipped_count > 0:
                    flash(f'選択された{skipped_count}試合は全て既に他の大会に登録済みです。', 'warning')
                else:
                    flash('追加できる新しい試合がありませんでした。', 'info')
        except Exception as e:
            db.session.rollback()
            print(f"単一追加 - データベースコミット時エラー: {e}")
            flash('試合の追加中にエラーが発生しました。管理者にお問い合わせください。', 'error')
        
        return redirect(url_for('main.view_tournament', tournament_id=tournament_id))
    
    # GET リクエスト：利用可能な試合一覧を表示
    # データベース全体で既に登録済みの試合のrcg_filenameを取得
    all_registered_matches = MatchResult.query.all()
    registered_match_ids = set()
    
    for match in all_registered_matches:
        # RCGファイル名からmatch_idを逆算
        csv_match_id = match.rcg_filename.replace('.rcg.gz', '').replace('.rcg', '')
        if not csv_match_id.endswith('.event'):
            csv_match_id += '.event'
        registered_match_ids.add(csv_match_id)
    
    print(f"🔍 データベース全体 - 登録済みmatch_ids: {len(registered_match_ids)}件")
    
    # 利用可能な全CSVデータを取得（match_id順でソート）
    available_matches = db.session.query(
        EventData.match_id,
        func.count(EventData.id).label('event_count')
    ).group_by(EventData.match_id).order_by(EventData.match_id).all()
    
    # 未登録の試合のみフィルタ
    unregistered_matches = []
    for match_id, event_count in available_matches:
        if match_id not in registered_match_ids:
            # match_idから試合情報を抽出
            try:
                parts = match_id.replace('.event', '').split('-')
                if len(parts) >= 6:
                    date_part = parts[0]
                    time_part = parts[1]
                    team1 = parts[2].replace('2024', '').replace('2023', '')
                    team2 = parts[3].replace('2024', '').replace('2023', '')
                    match_num = parts[4]
                    sim_part = parts[5]
                    
                    unregistered_matches.append({
                        'match_id': match_id,
                        'display_name': f"{team1} vs {team2} ({match_num}-{sim_part})",
                        'team1': team1,
                        'team2': team2,
                        'event_count': event_count,
                        'date_time': f"{date_part}-{time_part}"
                    })
            except:
                # パース失敗時はそのまま
                unregistered_matches.append({
                    'match_id': match_id,
                    'display_name': match_id,
                    'team1': 'Unknown',
                    'team2': 'Unknown',
                    'event_count': event_count,
                    'date_time': 'Unknown'
                })
    
    # match_idでソート（GIS検索と同じ並び順）
    unregistered_matches.sort(key=lambda x: x['match_id'])
    
    print(f"✅ 単一追加 - 全試合数: {len(available_matches)}, 登録済み: {len(registered_match_ids)}, 未登録: {len(unregistered_matches)}")
    
    return render_template('add_match_to_tournament.html', 
                         tournament=tournament,
                         available_matches=unregistered_matches,
                         registered_count=len(registered_match_ids))

@main.route('/tournament/<int:tournament_id>/delete_match/<int:match_id>', methods=['POST'])
def delete_match_from_tournament(tournament_id, match_id):
    """大会から試合を削除する"""
    from .models import Tournament
    tournament = Tournament.query.get_or_404(tournament_id)
    match = MatchResult.query.get_or_404(match_id)
    
    # 試合が指定された大会に属しているかチェック
    if match.tournament_id != tournament_id:
        flash('指定された試合は当該大会に登録されていません。', 'error')
        return redirect(url_for('main.view_tournament', tournament_id=tournament_id))
    
    try:
        db.session.delete(match)
        db.session.commit()
        flash(f'✅ 試合「{match.team1} vs {match.team2}」を大会から削除しました。', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'❌ 試合削除に失敗しました: {e}', 'error')
    
    return redirect(url_for('main.view_tournament', tournament_id=tournament_id))

@main.route('/api/event/<int:event_id>/sequence')
def get_event_sequence(event_id):
    """指定されたイベントが含まれるシーケンスを取得（EventSequenceテーブル活用）"""
    import json
    import time
    
    start_time = time.time()
    
    event = EventData.query.get_or_404(event_id)
    
    # EventSequenceテーブルから該当シーケンスを検索
    sequences = EventSequence.query.filter_by(
        match_id=event.match_id,
        team=event.side1
    ).all()
    
    # イベントIDがevent_idsに含まれるシーケンスを検索
    target_sequence = None
    for seq in sequences:
        if seq.event_ids:
            try:
                event_ids = json.loads(seq.event_ids)
                if event_id in event_ids:
                    target_sequence = seq
                    break
            except (json.JSONDecodeError, TypeError):
                continue
    
    if not target_sequence:
        return jsonify({
            'error': 'Sequence not found in database',
            'fallback': True,
            'message': f'Event {event_id} not found in any EventSequence for match {event.match_id}, team {event.side1}'
        }), 404
    
    # シーケンスに含まれるイベントデータを取得
    try:
        event_ids = json.loads(target_sequence.event_ids)
        sequence_events = EventData.query.filter(
            EventData.id.in_(event_ids)
        ).order_by(EventData.time1).all()
        
        processing_time = round(time.time() - start_time, 3)
        
        return jsonify({
            'sequence_info': {
                'id': target_sequence.id,
                'team': target_sequence.team,
                'sequence_number': target_sequence.sequence_number,
                'start_time': target_sequence.start_time,
                'end_time': target_sequence.end_time,
                'event_count': target_sequence.event_count,  # event_countフィールド活用
                'duration': target_sequence.end_time - target_sequence.start_time
            },
            'events': [{
                'id': e.id,
                'time1': e.time1,
                'time2': e.time2,
                'type': e.type,
                'side1': e.side1,
                'side2': e.side2,
                'unum1': e.unum1,
                'unum2': e.unum2,
                'x1': e.x1,
                'y1': e.y1,
                'x2': e.x2,
                'y2': e.y2,
                'mode1': e.mode1,
                'success': e.success
            } for e in sequence_events],
            'processing_time': processing_time,
            'source': 'EventSequence_database'
        })
        
    except (json.JSONDecodeError, TypeError) as e:
        return jsonify({
            'error': f'Invalid event_ids format in sequence {target_sequence.id}: {str(e)}',
            'fallback': True
        }), 500