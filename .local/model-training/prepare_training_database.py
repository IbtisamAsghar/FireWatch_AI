"""Provision an isolated password-protected research DB using the existing runtime."""
import json
import os
import secrets
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql

root = next(parent for parent in Path(__file__).resolve().parents if (parent / 'pyproject.toml').is_file())
home = root / '.local' / 'model-training'
home.mkdir(parents=True, exist_ok=True)
binary = root / '.local' / 'phase52' / 'pgsql' / 'bin'
record = home / 'database.json'
if record.exists():
    state = json.loads(record.read_text())
else:
    state = dict(port=55432, admin_password=secrets.token_urlsafe(36), app_password=secrets.token_urlsafe(36))
    record.write_text(json.dumps(state), encoding='utf-8')

def run(name, *args):
    result = subprocess.run([str(binary / (name + '.exe')), *map(str, args)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(name + ' failed; inspect the isolated database log')

if not (home / 'data' / 'PG_VERSION').exists():
    pw = home / 'init-password.tmp'
    pw.write_text(state['admin_password'])
    try:
        run('initdb', '-D', home / 'data', '-U', 'firewatch_bootstrap', '--encoding=UTF8', '--locale=C', '--auth=scram-sha-256', '--pwfile', pw)
    finally:
        pw.unlink(missing_ok=True)
    with (home / 'data' / 'postgresql.conf').open('a') as stream:
        stream.write("\nlisten_addresses = '127.0.0.1'\nport = 55432\ntimezone = 'UTC'\n")
status = subprocess.run([str(binary / 'pg_ctl.exe'), 'status', '-D', str(home / 'data')], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
if status.returncode:
    run('pg_ctl', 'start', '-D', home / 'data', '-l', home / 'server.log', '-w', '-t', '45')
with psycopg.connect(host='127.0.0.1', port=state['port'], dbname='postgres', user='firewatch_bootstrap', password=state['admin_password'], autocommit=True) as connection:
    if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='firewatch'").fetchone():
        connection.execute(sql.SQL('CREATE ROLE firewatch LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}').format(sql.Literal(state['app_password'])))
    if not connection.execute("SELECT 1 FROM pg_database WHERE datname='firewatch'").fetchone():
        connection.execute('CREATE DATABASE firewatch OWNER firewatch')
environment = dict(os.environ, DATABASE_URL=f"postgresql+psycopg://firewatch:{state['app_password']}@127.0.0.1:{state['port']}/firewatch")
result = subprocess.run([str(root / '.venv/Scripts/python.exe'), '-m', 'alembic', 'upgrade', 'head'], cwd=root, env=environment, capture_output=True, text=True)
if result.returncode:
    raise RuntimeError('Migration failed: ' + result.stderr[-1000:])
print('Isolated local research database ready on port 55432; schema migrated. Credentials were not displayed.')
