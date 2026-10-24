import json
import socket
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text

from firewatch.config import get_settings

s = get_settings()
print('Selected scorer:', s.active_scorer)
for port in (5432, 5433, 55432):
    with socket.socket() as sock:
        sock.settimeout(0.5)
        print('Local port', port, 'open:', sock.connect_ex(('127.0.0.1', port)) == 0)
try:
    engine = create_engine(s.database_url.get_secret_value(), connect_args={'connect_timeout': 5})
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION READ ONLY'))
        tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()" )).scalars().all()
        print('Application tables:', tables)
        for name in ('detections', 'source_assets', 'model_versions', 'model_activation'):
            if name in tables:
                print(name, 'rows:', connection.execute(text(f'SELECT count(*) FROM {name}')).scalar_one())
except Exception as exc:
    print('Database probe:', type(exc).__name__)
for year in (2022, 2024):
    manifest = json.loads(Path(f'docs/manifests/season-{year}-archive.json').read_text())
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            with client.stream('GET', manifest['source_url']) as response:
                print('NASA archive', year, 'HTTP', response.status_code, 'type:', response.headers.get('content-type'))
    except Exception as exc:
        print('NASA archive', year, type(exc).__name__)
