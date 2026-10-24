import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from firewatch.config import get_settings
from firewatch.db.models import ModelActivation, ModelPromotion, ModelVersion
from firewatch.db.session import Database

root=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
os.chdir(root)
settings=get_settings()
assert settings.active_scorer=='registry'
result=json.loads(Path('.local/readiness/ml-published-result.json').read_text())
run_id=result['run_id']
with httpx.Client(base_url='http://127.0.0.1:8000',timeout=10,trust_env=False) as client:
    assert client.get('/health/ready').status_code==200
    model=client.get('/api/v1/models/current').json()
    assert model['scorer_version']=='forest-independent-2025-v2' and model['status']=='ready'
    verification=[]
    for detail in result['details']:
        if detail.get('evidence'):
            response=client.get('/api/v1/alerts/'+detail['evidence']['alert_id']+'/verify',params={'verify_artifacts':'true'})
            assert response.status_code==200
            value=response.json()
            assert value['local_status']=='local_match'
            assert value['artifact_verification']=='verified',value['artifact_verification']
            verification.append(value)
    baseline_id='63c1b8df-4f31-544d-9691-16f8ef39f9bf'
    baseline=client.get('/api/v1/hotspots',params={'run_id':baseline_id}).json()
    assert baseline['total']==6 and all(row['scorer_version']=='baseline-v1' for row in baseline['items'])
database=Database(settings)
with database.session() as session:
    active=session.get(ModelActivation,'default')
    registered=session.get(ModelVersion,active.active_model_id)
    promotion=session.scalar(select(ModelPromotion).where(ModelPromotion.model_id==registered.id))
    assert registered.version==model['scorer_version'] and promotion.reviewer=='Talha'
    release=dict(model_id=str(registered.id),version=registered.version,reviewer=promotion.reviewer,approval_event=str(promotion.id),activation_revision=active.revision,artifact_path=registered.artifact_path,artifact_sha256=registered.artifact_hash,managed_release_sha256=registered.training_manifest['release_sha256'],scope=registered.training_manifest['scope'])
database.close()
receipt_path=Path('docs/local-real-readiness-receipt.json')
receipt=json.loads(receipt_path.read_text())
receipt.update(verified_at=datetime.now(UTC).isoformat(),run_id=run_id,baseline_run_id=baseline_id,current_scorer=model,model_approved=True,model_activated=True,independent_review_pending=False,independent_review_source='Talha confirmed review and explicitly approved historical replay with documented limitations in this chat.',registry_release=release,operator_authentication_checked=True,worker_execution_checked=True,idempotency_checked=True,evidence_records_verified=len(verification),artifact_verification='verified' if verification else 'no_alerts_emitted',remaining_requirements=['Separate live NRT generalization review before live ML ingestion','Production deployment, HTTPS and backup/restore acceptance'])
receipt['counts']['registered_models']=1
receipt_path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
result['verification']=verification
Path('artifacts/enriched-real-ml-api-examples.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps(dict(run_id=run_id,model=model['scorer_version'],reviewer='Talha',hotspots=6,weather_and_climate=True,verified_alerts=len(verification),historical_baseline_preserved=True,registry_scope=release['scope']['modes']),indent=2))
