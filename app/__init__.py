from flask import Flask
from sqlalchemy import text
from .models import db
import os


def create_app():
    app = Flask(__name__)

    # 設定の読み込み
    config_name = os.environ.get('FLASK_CONFIG') or 'default'
    from config import config
    app.config.from_object(config[config_name])

    db.init_app(app)

    with app.app_context():
        # PostGIS拡張が有効かチェックし、必要に応じて初期化
        with db.engine.connect() as conn:
            conn.execute(text('CREATE EXTENSION IF NOT EXISTS postgis;'))
            has_geometry = conn.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'geometry');")
            ).scalar()
            if not has_geometry:
                raise RuntimeError(
                    "PostGIS check failed: 'geometry' type not found after attempting to create extension."
                )
            conn.commit()

        db.create_all()

    from .views import main
    app.register_blueprint(main)

    return app
