"""Use a separate disposable database; never point tests at application data."""
import json
import os
import subprocess
from pathlib import Path

import psycopg

root=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
state=json.loads((root/'.local/model-training/database.json').read_text())
with psycopg.connect(host='127.0.0.1',port=state['port'],user='firewatch_bootstrap',password=state['admin_password'],dbname='postgres',autocommit=True) as connection:
    if not connection.execute("SELECT 1 FROM pg_database WHERE datname='firewatch_test'").fetchone():
        connection.execute('CREATE DATABASE firewatch_test OWNER firewatch')
environment=dict(os.environ,FIREWATCH_OFFLINE_TESTS='1',FIREWATCH_TEST_DATABASE_URL=f"postgresql+psycopg://firewatch:{state['app_password']}@127.0.0.1:{state['port']}/firewatch_test")
with (root/'.local/readiness/regression-v2.log').open('w',encoding='utf-8') as log:
    result=subprocess.run([str(root/'.venv/Scripts/python.exe'),'-m','pytest','-q','--basetemp=.local/readiness/pytest-full-ready-v2','--junitxml=artifacts/readiness-regression.xml'],cwd=root,env=environment,stdout=log,stderr=subprocess.STDOUT)
print('Regression exit:',result.returncode,'; log: .local/readiness/regression-v2.log',flush=True)
raise SystemExit(result.returncode)
