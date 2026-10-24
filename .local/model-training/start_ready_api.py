import json
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

root=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
directory=root/'.local/readiness'
with socket.socket() as probe:
    available=probe.connect_ex(('127.0.0.1',8000))!=0
if available:
    with (directory/'api.stdout.log').open('ab') as out, (directory/'api.stderr.log').open('ab') as err:
        process=subprocess.Popen([str(root/'.venv/Scripts/python.exe'),'-m','uvicorn','firewatch.main:app','--host','127.0.0.1','--port','8000'],cwd=root,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.CREATE_NEW_PROCESS_GROUP)
    (directory/'api-process.json').write_text(json.dumps(dict(pid=process.pid,port=8000,command='uvicorn firewatch.main:app',root=str(root))))
for attempt in range(20):
    try:
        response=httpx.get('http://127.0.0.1:8000/health/ready',timeout=2,trust_env=False)
        if response.status_code==200:
            result=httpx.get('http://127.0.0.1:8000/api/v1/runs',timeout=3,trust_env=False)
            assert result.status_code==200 and result.json()['total']>=2
            print('Backend serving on http://127.0.0.1:8000; readiness passed; real completed runs visible.')
            break
    except httpx.HTTPError:
        pass
    time.sleep(0.5)
else:
    raise RuntimeError('API startup failed; inspect local readiness logs')
