"""Exercise the real protected HTTP submission and dedicated-worker path."""
import json
import os
import time
from pathlib import Path

import httpx
import yaml

from firewatch.config import get_settings

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
os.chdir(ROOT)
settings=get_settings()
assert settings.active_scorer=='registry'
directory=ROOT/'.local/readiness'
scenario=yaml.safe_load((directory/'ml-scenario.yaml').read_text())
catalog=yaml.safe_load(settings.replays_config.read_text())
if not any(s['id']==scenario['id'] for s in catalog['scenarios']):
    catalog['scenarios'].append(scenario)
    settings.replays_config.write_text(yaml.safe_dump(catalog,sort_keys=False),encoding='utf-8')
headers={'Authorization':'Bearer '+settings.admin_api_token.get_secret_value(),'Idempotency-Key':'ready-ml-independent-2025-v2'}
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=10,trust_env=False) as client:
    for _ in range(30):
        try:
            metadata=client.get('/api/v1/models/current').json()
            if metadata.get('scorer_version')=='forest-independent-2025-v2' and metadata.get('status')=='ready':
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError('Reviewed model not ready in running API')
    assert client.post('/api/v1/jobs/replay',json={'scenario_id':scenario['id']},headers={'Idempotency-Key':'unauthenticated-ready-check'}).status_code==401
    accepted=client.post('/api/v1/jobs/replay',json={'scenario_id':scenario['id']},headers=headers)
    assert accepted.status_code==202,accepted.text
    job_id=accepted.json()['job_id']
    for _ in range(120):
        status=client.get('/api/v1/jobs/'+job_id,headers=headers)
        assert status.status_code==200
        job=status.json()
        if job['status']=='succeeded':
            break
        if job['status']=='failed':
            raise RuntimeError('Real ML replay job failed: '+str(job.get('error_code')))
        time.sleep(0.5)
    else:
        raise RuntimeError('Worker did not complete replay within the bounded check')
    run_id=job['result_run_id']
    data=client.get('/api/v1/hotspots',params={'run_id':run_id}).json()
    summary=client.get('/api/v1/summary',params={'run_id':run_id}).json()
    geojson=client.get('/api/v1/hotspots.geojson',params={'run_id':run_id}).json()
    assert data['total']==summary['total']==geojson['total']==6
    assert {r['snapshot_id'] for r in data['items']}=={f['properties']['snapshot_id'] for f in geojson['features']}
    assert sum(summary['priority_counts'].values())==6
    details=[]
    for row in data['items']:
        assert row['scorer_version']=='forest-independent-2025-v2'
        assert row['score_kind']=='model_score' and row['escalation_probability'] is None
        detail=client.get('/api/v1/hotspots/'+row['snapshot_id']).json()
        for name in ['temperature_c','humidity_pct','wind_ms','gust_ms','precipitation_24h_mm','temperature_departure_c','rainfall_deficit_24h_mm']:
            assert detail['features'][name] is not None,name
        details.append(detail)
    repeat=client.post('/api/v1/jobs/replay',json={'scenario_id':scenario['id']},headers=headers)
    assert repeat.status_code==202 and repeat.json()['job_id']==job_id
    assert repeat.headers['idempotency-replayed']=='true'
    output=dict(job_id=job_id,run_id=run_id,mode='replay',scenario_id=scenario['id'],status='completed',scorer_version=metadata['scorer_version'],hotspots=data['total'],actual_weather_and_climate_on_all_hotspots=True,operator_authentication_checked=True,idempotency_checked=True,model=metadata,summary=summary,hotspot_page=data,geojson=geojson,details=details)
    (directory/'ml-published-result.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({key:output[key] for key in ['job_id','run_id','mode','status','scorer_version','hotspots','actual_weather_and_climate_on_all_hotspots','operator_authentication_checked','idempotency_checked']},indent=2))
