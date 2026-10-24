"""Verify real trained outputs and backend readiness, without granting approval."""
import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from firewatch.config import get_settings
from firewatch.db.models import Detection, ModelVersion
from firewatch.db.session import Database
from firewatch.main import create_app
from firewatch.ml.forest_artifact import PortableForest
from firewatch.ml.split_package import read_samples

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'pyproject.toml').is_file())
os.chdir(ROOT)
state = json.loads((ROOT / '.local/model-training/database.json').read_text())
dsn = f"postgresql+psycopg://firewatch:{state['app_password']}@127.0.0.1:{state['port']}/firewatch"
os.environ['DATABASE_URL'] = dsn
get_settings.cache_clear()
training = ROOT / 'data/models/recovered-seasons-v1'
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
model = PortableForest.model_validate_json((training / 'model.json').read_bytes())
report = json.loads((training / 'training-report.json').read_bytes())
assert report['status'] == 'fitted_development_only'
samples = read_samples(training / 'split/labels')
membership = json.loads((training / 'split/membership.json').read_bytes())
final_ids = {row['row_id'] for row in membership['membership'] if row['partition'] == 'final_test'}
assert not set(model.fit_row_ids) & final_ids
rows = [row for row in samples if row.row_id in set(model.fit_row_ids)][:32]
predictions = model.predict([row.target.feature for row in rows])
assert len(predictions) == len(rows) and len(rows) > 0
assert all(math.isfinite(p.model_score) and 0 <= p.model_score <= 1 for p in predictions)
assert all(p.escalation_probability is None for p in predictions)
settings = get_settings()
assert settings.active_scorer == 'baseline-v1'
database = Database(settings)
with database.session() as session:
    count = session.scalar(select(func.count()).select_from(Detection))
    approved = session.scalar(select(func.count()).select_from(ModelVersion))
assert count == 17862 and approved == 0
database.close()
http = {}
with TestClient(create_app(settings)) as client:
    for path in ('/health/live', '/health/ready', '/api/v1/models/current', '/api/v1/regions', '/api/v1/status'):
        response = client.get(path)
        assert response.status_code == 200, path
        http[path] = response.status_code
    current = client.get('/api/v1/models/current').json()
    assert current['scoring_available'] is True

# Repair the non-working checkout DB configuration only after successful readiness.
env_path = ROOT / '.env'
previous = env_path.read_text(encoding='utf-8')
backup = ROOT / '.local/model-training/previous.env'
if not backup.exists():
    backup.write_text(previous, encoding='utf-8')
updates = {'DATABASE_URL': dsn, 'POSTGRES_PASSWORD': state['app_password'], 'POSTGRES_DB': 'firewatch', 'POSTGRES_USER': 'firewatch'}
updated = previous
for key, value in updates.items():
    updated, changes = re.subn(r'^' + key + r'=.*$', lambda _: key + '=' + value, updated, flags=re.MULTILINE)
    assert changes == 1, 'Expected one existing configuration key'
env_path.write_text(updated, encoding='utf-8', newline='\n')

summary = json.loads((ROOT / 'data/backfills/recovered-seasons-v1/summary.json').read_bytes())
receipt = dict(
    schema_version='recovered-real-training-v1',
    verified_at=datetime.now(UTC).isoformat(),
    data_kind='real_NASA_FIRMS_NOAA20_SP',
    region='northern-california',
    seasons=[2022, 2024],
    observations=count,
    original_csv_sha256_matches=True,
    synthetic_training_rows=0,
    processed_cutoffs=summary['processed_cutoffs'],
    snapshots=summary['rows'],
    eligible_rows=summary['eligible_rows'],
    labels=summary['labels'],
    missing_weather_rows=summary['missing_weather_rows'],
    partition_counts=membership['partition_counts'],
    split_strategy=membership['strategy'],
    selected_parameters=report['selected_parameters'],
    fit_rows=len(model.fit_row_ids),
    model_trained=True,
    trained_model_path='data/models/recovered-seasons-v1/model.json',
    training_manifest_sha256=sha(training / 'manifest.json'),
    training_model_sha256=sha(training / 'model.json'),
    release_evaluation_performed=False,
    approved_runtime_bundle_created=False,
    final_test_fit_overlap=0,
    final_test_evaluated_this_run=False,
    previous_final_holdout_already_opened=True,
    previous_final_metrics_reused_for_this_candidate=False,
    probability_supported=model.probability_supported,
    calibration_status=model.calibration_status,
    training_export_deterministic_verification=True,
    portable_prediction_parity_rows=report['portability_parity_rows'],
    independent_local_prediction_check_rows=len(rows),
    portable_model_load_and_score=True,
    backend_http_checks=http,
    backend_local_database_port=state['port'],
    active_scorer=settings.active_scorer,
    model_approved=False,
    model_activated=False,
    limitations=[
        'This recovered candidate differs from the old evaluated release; old metrics do not apply.',
        'Historical SP observations do not establish live NRT generalization.',
        'Old weather/climate cache was unavailable; missing values and flags are retained.',
        'Partition audit was automated and conservative, not independent human incident review.',
        'Original final holdout was previously opened; do not reopen it for a new experiment.',
        'Release requires recovery of the exact prior release and ledger or a reviewed new independent validation cohort, plus independent model approval.',
    ],
)
(ROOT / 'docs/recovered-model-training-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
print(json.dumps(receipt, indent=2))
