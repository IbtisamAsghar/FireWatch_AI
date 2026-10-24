import json
import os
import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from firewatch.config import get_settings
from firewatch.db.models import WeatherRecord
from firewatch.db.session import Database
from firewatch.main import create_app

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
os.chdir(ROOT)
directory=ROOT/'.local/readiness'
results=json.loads((directory/'published-results.json').read_text())
active=next(r for r in results if r['scenario_kind']=='active')
quiet=next(r for r in results if r['scenario_kind']=='quiet')
env_path=ROOT/'.env'
content=env_path.read_text()
updates={'REPLAYS_CONFIG':'.local/readiness/replays.yaml','REPLAY_BUNDLE_DIR':'data/replays-ready-v1','CORS_ORIGINS':'["http://localhost:3000","http://127.0.0.1:3000","http://localhost:5173","http://127.0.0.1:5173"]'}
for key,value in updates.items():
    if re.search('^'+key+'=',content,flags=re.MULTILINE):
        content=re.sub('^'+key+'=.*$',lambda _:key+'='+value,content,flags=re.MULTILINE)
    else:
        content+='\n'+key+'='+value+'\n'
env_path.write_text(content,encoding='utf-8',newline='\n')
get_settings.cache_clear()
settings=get_settings()
database=Database(settings)
with database.session() as session:
    weather_rows=session.scalar(select(func.count()).select_from(WeatherRecord))
assert weather_rows>=144
database.close()
examples={}
with TestClient(create_app(settings)) as client:
    for path in ['/health/ready','/api/v1/models/current','/api/v1/regions','/api/v1/runs','/api/v1/replays','/api/v1/status',f"/api/v1/hotspots?run_id={active['run_id']}",f"/api/v1/hotspots.geojson?run_id={active['run_id']}",f"/api/v1/summary?run_id={active['run_id']}",f"/api/v1/hotspots?run_id={quiet['run_id']}"]:
        response=client.get(path)
        assert response.status_code==200,path
        examples[path]=response.json()
    hotspots=examples[f"/api/v1/hotspots?run_id={active['run_id']}"]
    assert hotspots['total']==6
    assert examples[f"/api/v1/summary?run_id={active['run_id']}"]['total']==6
    assert examples[f"/api/v1/hotspots?run_id={quiet['run_id']}"]['total']==0
    weather_checks=[]
    for row in hotspots['items']:
        path='/api/v1/hotspots/'+row['snapshot_id']
        response=client.get(path)
        assert response.status_code==200
        detail=response.json()
        examples[path]=detail
        values=detail['features']
        names=['temperature_c','humidity_pct','wind_ms','gust_ms','precipitation_24h_mm']
        actual={name:values[name] for name in names}
        assert all(value is not None for value in actual.values())
        weather_checks.append(dict(snapshot_id=row['snapshot_id'],values=actual))
        evidence=detail.get('evidence')
        if evidence:
            verify_path='/api/v1/alerts/'+evidence['alert_id']+'/verify'
            verified=client.get(verify_path)
            assert verified.status_code==200
            assert verified.json()['local_status']=='local_match'
            examples[verify_path]=verified.json()
    preflight=client.options('/api/v1/regions',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'GET'})
    assert preflight.status_code==200
    assert preflight.headers['access-control-allow-origin']=='http://localhost:5173'
Path('artifacts/readiness-api-examples.json').write_text(json.dumps(examples,indent=2)+'\n',encoding='utf-8')
(directory/'weather-detail-checks.json').write_text(json.dumps(weather_checks,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(backend_ready=True,active_run_id=active['run_id'],quiet_run_id=quiet['run_id'],hotspots=6,weather_rows=weather_rows,hotspot_details_with_real_weather=len(weather_checks),api_examples='artifacts/readiness-api-examples.json',active_scorer=settings.active_scorer),indent=2))
