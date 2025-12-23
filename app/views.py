import csv
import json
import os
import time
from flask import Blueprint, render_template, request, redirect, url_for, current_app, jsonify, flash
from .models import db, MatchResult, EventData
from sqlalchemy import text, func, or_, and_
from geoalchemy2.functions import ST_Contains, ST_Intersects, ST_DWithin
from werkzeug.utils import secure_filename

main = Blueprint('main', __name__)

@main.route('/')
def index():
    from .models import Tournament, EventData
    
    tournaments = Tournament.query.order_by(Tournament.year.desc()).all()
    
    # 登録済みのmatch_id数を取得（件数表示用）
    match_data = db.session.query(EventData.match_id).distinct().all()
    
    return render_template('index.html', tournaments=tournaments, match_data=match_data)

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

@main.route('/event_search')
def event_search():
    start_time = time.time()
    
    selected_type = request.args.get('event_type')
    selected_team = request.args.get('team')
    selected_unum = request.args.get('unum')
    selected_success = request.args.get('success')
    selected_match = request.args.get("match_id")
    selected_mode = request.args.get('mode')
    x_min = request.args.get('x_min')
    x_max = request.args.get('x_max')
    y_min = request.args.get('y_min')
    y_max = request.args.get('y_max')
    min_time1 = request.args.get("min_time1")
    max_time1 = request.args.get("max_time1")
    match_ids = [m[0] for m in db.session.query(EventData.match_id).distinct().order_by(EventData.match_id).all()]
    page = request.args.get("page", 1, type=int)
    per_page = 500  # 一度に表示するイベント数

    query = EventData.query

    # 基本フィルタ処理
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
    if min_time1:
        try:
            query = query.filter(EventData.time1 >= float(min_time1))
        except ValueError:
            pass
    if max_time1:
        try:
            query = query.filter(EventData.time1 <= float(max_time1))
        except ValueError:
            pass

    # 複数矩形範囲処理
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

    # ページネーション
    import math
    from collections import defaultdict
    
    total_items = query.count()
    total_pages = math.ceil(total_items / per_page)
    pagination = query.order_by(EventData.match_id, EventData.time1).paginate(page=page, per_page=per_page, error_out=False)
    events = pagination.items

    # match_idでグループ化
    events_by_match = defaultdict(list)
    for event in events:
        events_by_match[event.match_id].append(event)

    # 検索フォーム選択肢
    event_types = [et[0] for et in db.session.query(EventData.type).distinct().order_by(EventData.type)]
    teams = [t[0] for t in db.session.query(EventData.side1).distinct().order_by(EventData.side1)]
    modes = [m[0] for m in db.session.query(EventData.mode1).distinct().order_by(EventData.mode1)]
    unums = [u[0] for u in db.session.query(EventData.unum1).distinct().order_by(EventData.unum1) if u[0] is not None]

    # 処理時間を計測
    processing_time = round(time.time() - start_time, 3)

    return render_template(
        'event_search.html',
        event_types=event_types,
        teams=teams,
        modes=modes,
        unums=unums,
        match_ids=match_ids,
        events_by_match=events_by_match,
        pagination=pagination,
        page=page,
        total_pages=total_pages,
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
        min_time1=request.args.get("min_time1", ""),
        max_time1=request.args.get("max_time1", ""),
        rects_json=request.args.get("rects_json", ""),
        processing_time=processing_time
    )

@main.route('/upload_event_csv', methods=['GET', 'POST'])
def upload_event_csv():
    if request.method == 'POST':
        db.session.expunge_all()
        db.session.rollback()
        
        files = request.files.getlist('file')  # 複数ファイル対応
        uploaded_files = []
        total_events = 0
        
        for file in files:
            if file and file.filename.endswith('.csv'):
                filename = secure_filename(file.filename)
                match_id = os.path.splitext(filename)[0]  # ファイル名を match_id に使う
                print(f"★処理中: file.filename={file.filename}, match_id={match_id}")
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
                
                uploaded_files.append({'filename': filename, 'events': event_count})
                total_events += event_count
        
        db.session.commit()
        print(f"✅ {len(uploaded_files)}個のファイル、{total_events}個のイベントをデータベースに登録完了")
        
        # リクエストの参照元に応じてリダイレクト先を決定
        referrer = request.referrer
        if referrer and 'upload_event_csv' not in referrer:
            # トップページからのアップロードの場合はトップページに戻る
            flash(f'✅ {len(uploaded_files)}個のCSVファイルを正常にアップロードしました。（合計 {total_events} イベント）', 'success')
            return redirect(url_for('main.index'))
        else:
            # 直接アクセスの場合はイベント検索へ
            return redirect(url_for('main.event_search'))

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
                    
                    # 既存の試合をチェック（重複防止） - RCGファイル名とmatch_idの両方でチェック
                    existing_match = MatchResult.query.filter_by(
                        tournament_id=tournament_id,
                        rcg_filename=rcg_filename
                    ).first()
                    
                    if existing_match:
                        print(f"⚠️ 既に登録済みの試合をスキップ: {rcg_filename}")
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
        
        if added_count > 0:
            db.session.commit()
            flash(f'✅ {added_count}試合を大会に追加しました。', 'success')
        else:
            flash('追加できる新しい試合がありませんでした。', 'info')
        
        return redirect(url_for('main.view_tournament', tournament_id=tournament_id))
    
    # GET: 利用可能な試合を表示
    # 既に大会に登録されている試合を取得
    registered_matches = MatchResult.query.filter_by(tournament_id=tournament_id).all()
    
    # 登録済み試合のmatch_idセットを作成（より確実な比較のため）
    registered_match_ids = set()
    for match in registered_matches:
        # RCGファイル名からmatch_idを逆算
        csv_match_id = match.rcg_filename.replace('.rcg.gz', '').replace('.rcg', '')
        if not csv_match_id.endswith('.event'):
            csv_match_id += '.event'
        registered_match_ids.add(csv_match_id)
    
    print(f"🔍 登録済みmatch_ids: {registered_match_ids}")
    
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
                         registered_count=len(registered_matches))

@main.route('/event_sequence_search')
def event_sequence_search():
    match_id = request.args.get("match_id")
    events = EventData.query.filter_by(match_id=match_id).order_by(EventData.time1).all()

    sequences = []
    current_seq = []
    current_team = None

    for e in events:
        # 不自然なInterception（得点後の戻し処理など）を除外条件に
        is_reset = (
            e.type == "Interception" and
            e.mode1 == "play_on" and
            e.x2 == 0.0 and e.y2 == 0.0
        )

        if is_reset:
            if current_seq:
                sequences.append(current_seq)
                current_seq = []
            current_team = None
            continue

        if current_team is None:
            current_team = e.side1
            current_seq.append(e)
        elif e.side1 == current_team:
            current_seq.append(e)

            if e.side2 != current_team:
                sequences.append(current_seq)
                current_seq = []
                current_team = None
        else:
            if current_seq:
                sequences.append(current_seq)
                current_seq = []
            current_team = e.side1
            current_seq.append(e)

    if current_seq:
        sequences.append(current_seq)

    # 可視化用辞書に変換
    sequence_dicts = []
    for seq in sequences:
        sequence_dicts.append([
            {
                "type": e.type,
                "x1": e.x1,
                "y1": e.y1,
                "x2": e.x2,
                "y2": e.y2,
                "time1": e.time1,
                "time2": e.time2,
                "side1": e.side1,
                "side2": e.side2,
                "unum1": e.unum1,
                "unum2": e.unum2,
                "mode1": e.mode1
            } for e in seq
        ])

    return render_template("event_sequence.html", sequences=sequence_dicts)

@main.route("/event_sequence_filtered")
def event_sequence_filtered():
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

    # 連続イベント抽出
    sequences = []
    current_sequence = []
    current_team = None
    tracking = False  # 追跡中フラグ
    last_mode = None

    def end_current_sequence():
        """現在のシーケンスを終了し、リストに追加する"""
        nonlocal current_sequence, current_team, tracking
        if current_sequence:
            sequences.append(current_sequence)
            current_sequence = []
        current_team = None
        tracking = False

    for i, event in enumerate(filtered_events):
        # 不自然なInterception（得点後の戻し処理など）を除外
        is_reset = (
            event.type == "Interception" and
            event.mode1 == "play_on" and
            event.x2 == 0.0 and event.y2 == 0.0
        )

        if is_reset:
            # 現在のシーケンスがあれば終了
            if tracking and current_sequence:
                end_current_sequence()
            last_mode = event.mode1
            continue

        if not tracking:
            # 起点として認めるか？（矩形条件があるなら矩形内のみ）
            if rects and not is_in_any_rect(event):
                continue

            # neutralまたは相手チームへの単発イベントは個別シーケンス
            if event.side2 == "neutral" or (event.side2 and event.side2 != event.side1):
                sequences.append([event])  # 1件だけのシーケンスとして記録
                last_mode = event.mode1
                continue

            # 追跡開始
            current_sequence = [event]
            current_team = event.side1
            tracking = True
            last_mode = event.mode1
            continue
        
        # 続行前に、前のイベントとの間に他チームのイベントがないかチェック
        if (tracking and current_sequence and 
            has_interrupting_events(current_sequence[-1].time1, event.time1, current_team)):
            # 間に他チームのイベントがある場合はシーケンスを終了
            end_current_sequence()
            # 新しいシーケンスとして開始するかチェック
            if rects and not is_in_any_rect(event):
                last_mode = event.mode1
                continue
            # neutralまたは相手チームへの単発イベント処理
            if event.side2 == "neutral" or (event.side2 and event.side2 != event.side1):
                sequences.append([event])
                last_mode = event.mode1
                continue
            # 新しいシーケンス開始
            current_sequence = [event]
            current_team = event.side1
            tracking = True
            last_mode = event.mode1
            continue
        
        # チームが変わった場合
        if event.side1 and event.side1 != current_team:
            end_current_sequence()
            # 新しいシーケンスとして開始するかチェック
            if rects and not is_in_any_rect(event):
                last_mode = event.mode1
                continue
            # neutralまたは相手チームへの単発イベント処理
            if event.side2 == "neutral" or (event.side2 and event.side2 != event.side1):
                sequences.append([event])
                last_mode = event.mode1
                continue
            # 新しいシーケンス開始
            current_sequence = [event]
            current_team = event.side1
            tracking = True
            last_mode = event.mode1
            continue

        # ボールが neutral または相手チームに渡ったら終了
        if (event.side2 == "neutral" or 
            (event.side2 and event.side2 != current_team) or 
            (event.side1 and event.side1 != current_team)):
            current_sequence.append(event)
            end_current_sequence()
            last_mode = event.mode1
            continue

        # 続行
        current_sequence.append(event)
        last_mode = event.mode1

    # 最後のシーケンスが残っていれば追加
    if tracking and current_sequence:
        sequences.append(current_sequence)

    # JSON変換用
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
                           rects_json=rects_json)

@main.route('/interactive_gis_map')
def interactive_gis_map():
    """インタラクティブGISマップページ"""
    start_time = time.time()
    print("🔍 interactive_gis_map called")
    
    # event_searchと同様のデータ処理ロジックを追加
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

    # フィルタ処理（event_searchと同じ）
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

    # 処理時間を計測（データ取得後）
    processing_time = round(time.time() - start_time, 3)

    return render_template('interactive_gis_map.html',
                           events=map_events,  # マップ表示用データ
                           list_events=list_events,  # イベントリスト表示用データ
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
        for csv_match_id in selected_match_ids:
            # CSVのmatch_idからRCGファイル名を逆算
            rcg_filename = csv_match_id.replace('.event', '.rcg')
            
            # 既に登録済みかチェック
            existing_match = MatchResult.query.filter_by(
                tournament_id=tournament_id,
                rcg_filename=rcg_filename
            ).first()
            
            if existing_match:
                print(f"⚠️ 単一追加 - 既に登録済みの試合をスキップ: {rcg_filename}")
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
        
        if added_count > 0:
            db.session.commit()
            flash(f'✅ {added_count}試合を大会に追加しました。', 'success')
        else:
            flash('追加できる新しい試合がありませんでした。', 'info')
        
        return redirect(url_for('main.view_tournament', tournament_id=tournament_id))
    
    # GET リクエスト：利用可能な試合一覧を表示
    # 既に登録済みの試合のmatch_idを取得
    registered_matches = MatchResult.query.filter_by(tournament_id=tournament_id).all()
    registered_match_ids = set()
    
    for match in registered_matches:
        # RCGファイル名からmatch_idを逆算
        csv_match_id = match.rcg_filename.replace('.rcg.gz', '').replace('.rcg', '')
        if not csv_match_id.endswith('.event'):
            csv_match_id += '.event'
        registered_match_ids.add(csv_match_id)
    
    print(f"🔍 単一追加 - 登録済みmatch_ids: {registered_match_ids}")
    
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
                         registered_count=len(registered_matches))

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