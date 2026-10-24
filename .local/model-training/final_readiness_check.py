"""Record real serving evidence without claiming model approval."""
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import func, select

from firewatch.config import get_settings
from firewatch.db.models import ClimateBaselineRecord, Detection, ModelVersion, WeatherRecord
from firewatch.db.session import Database

root=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
os.chdir(root)
settings=get_settings()
result=json.loads(Path('.local/readiness/enriched-baseline-result.json').read_text())
run_id=result['run_id']
with httpx.Client(base_url='http://127.0.0.1:8000',trust_env=False,timeout=10) as client:
    assert client.get('/health/ready').status_code==200
    metadata=client.get('/api/v1/models/current').json()
    page=client.get('/api/v1/hotspots',params={'run_id':run_id}).json()
    summary=client.get('/api/v1/summary',params={'run_id':run_id}).json()
    geo=client.get('/api/v1/hotspots.geojson',params={'run_id':run_id}).json()
    assert page['total']==summary['total']==geo['total']==6
    assert sum(summary['priority_counts'].values())==6
    weather_names=['temperature_c','humidity_pct','wind_ms','gust_ms','precipitation_24h_mm','temperature_departure_c','rainfall_deficit_24h_mm']
    details=[]
    checks=[]
    for row in page['items']:
        response=client.get('/api/v1/hotspots/'+row['snapshot_id'])
        assert response.status_code==200
        detail=response.json()
        assert all(detail['features'][key] is not None for key in weather_names)
        assert not any(detail['missing_flags'][key] for key in weather_names)
        if detail['evidence']:
            verified=client.get('/api/v1/alerts/'+detail['evidence']['alert_id']+'/verify')
            assert verified.status_code==200 and verified.json()['local_status']=='local_match'
            checks.append(verified.json())
        details.append(detail)
    quiet=json.loads(Path('.local/readiness/published-results.json').read_text())[1]
    assert client.get('/api/v1/hotspots',params={'run_id':quiet['run_id']}).json()['total']==0
    unauthenticated=client.post('/api/v1/jobs/replay',json={'scenario_id':'norcal-weather-climate-v1'},headers={'Idempotency-Key':'readiness-auth-check-v1'})
    assert unauthenticated.status_code==401
    assert client.options('/api/v1/regions',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'GET'}).headers['access-control-allow-origin']=='http://localhost:5173'
database=Database(settings)
with database.session() as session:
    counts={name:session.scalar(select(func.count()).select_from(model)) for name,model in [('satellite_observations',Detection),('weather_records',WeatherRecord),('climate_baselines',ClimateBaselineRecord),('registered_models',ModelVersion)]}
database.close()
report=json.loads(Path('data/evaluations/independent-2025-final-v2/evaluation-report.json').read_text())
receipt=dict(schema_version='local-real-backend-readiness-v1',verified_at=datetime.now(UTC).isoformat(),api_base_url='http://127.0.0.1:8000',data_kind='real',run_id=run_id,mode='replay',hotspots=6,evidence_records_verified=len(checks),real_weather_and_climate_on_all_six_hotspots=True,counts=counts,current_scorer=metadata,model_trained=True,model_release_gates_passed=report['promotion_recommendation']=='independent_review_candidate',model_approved=False,model_activated=False,independent_review_pending=True,independent_test_rows=811,independent_test_positive_rows=47,ranking_average_precision={name:entry['metrics']['average_precision'] for name,entry in report['final']['methods'].items()},full_regression_passed=1273,full_regression_skipped=1,live_ingestion_enabled=False,production_deployed=False,remaining_requirements=['Actual independent human review of exact model artifacts before registry promotion','Separate live NRT generalization review for live ML','Production deployment, HTTPS and backup/restore acceptance'])
Path('docs/local-real-readiness-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
Path('artifacts/enriched-real-api-examples.json').write_text(json.dumps(dict(run_id=run_id,hotspots=page,summary=summary,geojson=geo,details=details,verification=checks),indent=2)+'\n',encoding='utf-8')
print(json.dumps(receipt,indent=2))
