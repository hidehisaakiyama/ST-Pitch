from flask import Flask
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
        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                conn.execute(text('CREATE EXTENSION IF NOT EXISTS postgis;'))
                conn.commit()
        except Exception as e:
            print(f"PostGIS extension setup: {e}")
        
        db.create_all()

    from .views import main
    app.register_blueprint(main)

    return app