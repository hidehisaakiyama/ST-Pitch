from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from geoalchemy2 import Geometry
from sqlalchemy import text

db = SQLAlchemy()

class Tournament(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    year = db.Column(db.Integer, nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)

    matches = db.relationship('MatchResult', backref='tournament', lazy=True)

    def __repr__(self):
        return f"<Tournament {self.name}>"


class MatchResult(db.Model):
    id = db.Column(db.Integer, primary_key=True)

    datetime = db.Column(db.DateTime, nullable=False)
    team1 = db.Column(db.String(100), nullable=False)
    team2 = db.Column(db.String(100), nullable=False)
    team1_score = db.Column(db.Integer, nullable=False)
    team2_score = db.Column(db.Integer, nullable=False)

    rcg_filename = db.Column(db.String(255), unique=True, nullable=False)

    tournament_id = db.Column(db.Integer, db.ForeignKey('tournament.id'), nullable=False)

    def __repr__(self):
        return f"<Match {self.team1} vs {self.team2} at {self.datetime}>"

class MatchEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.Integer, db.ForeignKey('match_result.id'), nullable=False)
    cycle = db.Column(db.Integer, nullable=False)
    event_type = db.Column(db.String(50), nullable=False)
    team = db.Column(db.String(10))
    player = db.Column(db.String(50))
    description = db.Column(db.Text)


class EventData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    type = db.Column(db.String(50), nullable=False)
    side1 = db.Column(db.String(10))
    unum1 = db.Column(db.Integer)
    time1 = db.Column(db.Integer)
    mode1 = db.Column(db.String(50))
    x1 = db.Column(db.Float)
    y1 = db.Column(db.Float)
    side2 = db.Column(db.String(10))
    unum2 = db.Column(db.Integer)
    time2 = db.Column(db.Integer)
    x2 = db.Column(db.Float)
    y2 = db.Column(db.Float)
    success = db.Column(db.Boolean)
    match_id = db.Column(db.String(200))
    
    # GIS関連のカラム
    start_point = db.Column(Geometry('POINT', srid=4326))  # 開始地点
    end_point = db.Column(Geometry('POINT', srid=4326))    # 終了地点
    movement_line = db.Column(Geometry('LINESTRING', srid=4326))  # 移動線
    
    def __repr__(self):
        return f"<Event {self.type} from {self.side1}#{self.unum1} at ({self.x1},{self.y1})>"
    
    @property
    def start_point_wkt(self):
        """開始地点のWKT表現を返す"""
        if self.x1 is not None and self.y1 is not None:
            return f"POINT({self.x1} {self.y1})"
        return None
    
    @property
    def end_point_wkt(self):
        """終了地点のWKT表現を返す"""
        if self.x2 is not None and self.y2 is not None:
            return f"POINT({self.x2} {self.y2})"
        return None
    
    @property
    def movement_line_wkt(self):
        """移動線のWKT表現を返す"""
        if all(x is not None for x in [self.x1, self.y1, self.x2, self.y2]):
            return f"LINESTRING({self.x1} {self.y1}, {self.x2} {self.y2})"
        return None


class EventSequence(db.Model):
    """イベントシーケンスを格納するテーブル"""
    id = db.Column(db.Integer, primary_key=True)
    match_id = db.Column(db.String(200), nullable=False)
    team = db.Column(db.String(10), nullable=False)
    sequence_number = db.Column(db.Integer, nullable=False)
    start_time = db.Column(db.Integer, nullable=False)
    end_time = db.Column(db.Integer, nullable=False)
    event_count = db.Column(db.Integer, nullable=False)
    
    # シーケンス全体の軌跡を表すLINESTRING
    trajectory = db.Column(Geometry('LINESTRING', srid=4326))
    # シーケンスの開始・終了地点
    start_point = db.Column(Geometry('POINT', srid=4326))
    end_point = db.Column(Geometry('POINT', srid=4326))
    # シーケンスが通過したエリア（凸包）
    coverage_area = db.Column(Geometry('POLYGON', srid=4326))
    
    # 関連するイベントのIDリスト（JSON形式）
    event_ids = db.Column(db.Text)
    
    def __repr__(self):
        return f"<EventSequence {self.team} #{self.sequence_number} ({self.start_time}-{self.end_time})>"
