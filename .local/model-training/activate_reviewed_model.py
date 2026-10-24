"""Normal guarded promotion only, using an actual completed independent review."""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from firewatch.config import get_settings
from firewatch.main import create_app
from firewatch.ml.registry_artifacts import ApprovalReview

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
os.chdir(ROOT)
review_path=Path(sys.argv[1]).resolve()
review=ApprovalReview.model_validate_json(review_path.read_bytes())
assert [m.value for m in review.supported_modes]==['replay']
assert not review.live_generalization_reviewed
evaluation=Path('data/evaluations/independent-2025-final-v2')
bundle=Path('data/inference/independent-2025-v2')
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
assert review.evaluation_manifest_sha256==sha(evaluation/'manifest.json')
assert review.inference_manifest_sha256==sha(bundle/'manifest.json')
assert review.model_card_sha256==sha(evaluation/'model-card.md')
result=subprocess.run([str(ROOT/'.venv/Scripts/firewatch.exe'),'promote-model','--version','forest-independent-2025-v2','--evaluation',str(evaluation),'--evaluation-sha256',review.evaluation_manifest_sha256,'--bundle',str(bundle),'--manifest-sha256',review.inference_manifest_sha256,'--review',str(review_path)],cwd=ROOT,capture_output=True,text=True)
if result.returncode:
    print(result.stdout)
    print(result.stderr)
    raise SystemExit(result.returncode)
settings=get_settings().model_copy(update={'active_scorer':'registry'})
with TestClient(create_app(settings)) as client:
    assert client.get('/health/ready').status_code==200
    response=client.get('/api/v1/models/current')
    assert response.status_code==200
    metadata=response.json()
    assert metadata['scorer_version']=='forest-independent-2025-v2'
    assert metadata['status']=='ready' and metadata['scoring_available']
    assert metadata['model_id'] is not None and not metadata['probability_supported']
env_path=ROOT/'.env'
content=env_path.read_text()
backup=ROOT/'.local/readiness/pre-registry.env'
if not backup.exists():
    backup.write_text(content,encoding='utf-8')
content,count=re.subn('^ACTIVE_SCORER=.*$','ACTIVE_SCORER=registry',content,flags=re.MULTILINE)
assert count==1
env_path.write_text(content,encoding='utf-8',newline='\n')
(ROOT/'.local/readiness/activated-model.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
print(result.stdout)
print('Approved registry model verified and selected in local configuration. Restart API/worker next.')
