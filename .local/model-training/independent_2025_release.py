"""Execute the frozen independent-2025 experiment; never invent human approval."""
import csv
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

from firewatch.config import get_settings
from firewatch.db.session import Database
from firewatch.evidence.canonical import canonical_bytes
from firewatch.ml.backfill import merge_backfill_labels, run_backfill
from firewatch.ml.dataset import load_design
from firewatch.ml.events import EventDecision, EventReview, UnionFind, build_event_catalog
from firewatch.ml.final_test_ledger import FinalTestLedger
from firewatch.ml.forest_package import read_forest_data
from firewatch.ml.labels import OutageCatalog
from firewatch.ml.release_evaluation import holdout_key
from firewatch.ml.split_package import _write, load_event_spec, load_source_observations
from firewatch.processing.features import load_feature_spec
from firewatch.repositories.archive import import_prepared
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.archive_manifest import ArchiveManifest
from firewatch.sources.archive_stage import prepare_archive
from firewatch.sources.firms import FirmsAreaRequest, FirmsClient
from firewatch.sources.firms_handoff import export_firms_handoff
from firewatch.sources.firms_normalize import NormalizationPolicy, load_saved_firms_batch

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
os.chdir(ROOT)
settings=get_settings()
work=Path('data/independent-2025-v1')
work.mkdir(exist_ok=True)
sources=Path('data/sources/independent-2025-v1')
labels=Path('data/labels/independent-2025-v1')
splits=Path('data/splits/independent-2025-v2')
training=Path('data/models/independent-2025-v2')
development=Path('data/evaluations/independent-2025-development-v2')
final=Path('data/evaluations/independent-2025-final-v2')
bundle=Path('data/inference/independent-2025-v2')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def cli(*args):
    print('Running:', ' '.join(map(str,args)),flush=True)
    result=subprocess.run([str(ROOT/'.venv/Scripts/firewatch.exe'),*map(str,args)],cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Stage failed: '+str(args[0]))

stage=sys.argv[1]
if stage=='amend-v2':
    data=read_forest_data(Path('data/models/independent-2025-v1/split'))
    key=holdout_key(data)
    ledger=FinalTestLedger(Path('data/evaluations/final-test-ledger.sqlite3'))
    assert ledger.get(key) is None,'Cannot amend after final access'
    paths=['docs/2025-validation-pre-test-amendment-v2.md','src/firewatch/ml/splits.py','src/firewatch/ml/preprocessing.py','src/firewatch/ml/labels.py','src/firewatch/ml/split_package.py','configs/target-dataset-2025-holdout-v1.yaml','configs/splits-2025-holdout-v1.yaml','configs/forest-v1.yaml','configs/evaluation-v1.yaml']
    _write(work/'protocol-amendment-v2.json',dict(schema_version='independent-2025-pre-final-amendment-v2',recorded_at=datetime.now(UTC).isoformat(),original_protocol_sha256=sha(work/'protocol.json'),files_sha256={p:sha(Path(p)) for p in paths},holdout_key=key,final_test_unopened=True,reason='Correct generic CV development-end support/event guard before any final scores. No cohort dates, features, selection objectives or release gates changed; rebuild split/model/development artifacts into new v2 paths.'))
    print('Pre-final audit correction pinned; new final ledger still unclaimed.',flush=True)
elif stage=='freeze':
    paths=['docs/2025-independent-validation-protocol.md','configs/target-dataset-2025-holdout-v1.yaml','configs/splits-2025-holdout-v1.yaml','configs/forest-v1.yaml','configs/evaluation-v1.yaml','configs/features-v1.yaml','configs/event-groups-v1.yaml']
    frozen=work/'protocol.json'
    pins={p:sha(Path(p)) for p in paths}
    if frozen.exists():
        assert json.loads(frozen.read_text())['files_sha256']==pins,'Frozen protocol changed'
    else:
        _write(frozen,dict(schema_version='independent-2025-protocol-freeze-v1',frozen_at=datetime.now(UTC).isoformat(),files_sha256=pins,final_holdout='2025-05-01/2025-11-01',prior_holdout_excluded_from_development=True,final_predictions_inspected=False))
    old_key=holdout_key(read_forest_data(Path('data/models/recovered-seasons-v1/split')))
    ledger=FinalTestLedger(Path('data/evaluations/final-test-ledger.sqlite3'))
    old=json.loads(Path('docs/ranking-release-receipt.json').read_text())
    if ledger.get(old_key) is None:
        with sqlite3.connect(ledger.path) as connection:
            connection.execute('INSERT INTO claims VALUES (?,?,?,?,?,?)',(old_key,old['development_manifest_sha256'],str(Path('data/evaluations/cadence-final-v1').resolve()),'blocked_missing_original_ledger',datetime.now(UTC).isoformat(),None))
    _write(work/'consumed-2024-holdout-block.json',dict(holdout_key=old_key,state='blocked_missing_original_ledger',evidence='docs/ranking-release-receipt.json',evidence_sha256=sha(Path('docs/ranking-release-receipt.json')),original_ledger_restored=False,reason='Public receipt records prior final access. This recovery entry prevents a new experiment reopening that period; it does not recreate original ledger history.'))
    print('Protocol pinned; known-consumed 2024 holdout blocked. New 2025 test remains unopened.',flush=True)
elif stage=='sources':
    frozen=json.loads((work/'protocol.json').read_text())
    assert all(sha(Path(p))==h for p,h in frozen['files_sha256'].items())
    chunks=work/'fetches.json'
    records=json.loads(chunks.read_text()) if chunks.exists() else []
    first,last=date(2025,4,30),date(2025,11,1)
    with FirmsClient(settings.firms_map_key,output_dir=settings.raw_data_dir/'firms') as client:
        day=first
        while day<=last:
            days=min(5,(last-day).days+1)
            existing=next((r for r in records if r['start']==day.isoformat() and r['days']==days),None)
            if existing is None:
                batch=client.fetch(FirmsAreaRequest(product='VIIRS_NOAA20_SP',bbox=BoundingBox(west=-122.5,south=38.5,east=-120,north=40.5),start_date=day,days=days))
                existing=dict(start=day.isoformat(),days=days,manifest=str(batch.manifest_path),raw_sha256=batch.asset.content_hash,rows=batch.row_count)
                records.append(existing)
                _write(chunks,records)
                print('Source windows recovered:',len(records),'/ 38; start:',day,'rows:',batch.row_count,flush=True)
            day+=timedelta(days=days)
    columns=None
    rows=[]
    retrieved=[]
    for record in records:
        batch=load_saved_firms_batch(Path(record['manifest']))
        assert batch.asset.content_hash==record['raw_sha256']
        assert columns is None or columns==batch.columns
        columns=batch.columns
        rows.extend(batch.rows)
        retrieved.append(batch.retrieved_at)
    buffer=io.StringIO(newline='')
    writer=csv.DictWriter(buffer,fieldnames=columns,lineterminator='\n')
    writer.writeheader(); writer.writerows(rows)
    source_csv=work/'assembled-real-2025.csv'
    source_csv.write_text(buffer.getvalue(),encoding='utf-8',newline='')
    counts=Counter(row['acq_date'] for row in rows)
    empty=[first+timedelta(days=i) for i in range((last-first).days+1) if counts[(first+timedelta(days=i)).isoformat()]==0]
    manifest=ArchiveManifest.model_validate(dict(schema_version='firms-archive-manifest-v1',trusted_local_input=True,source_url='https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html',source_kind='area_api_sample',product='VIIRS_NOAA20_SP',sensor='NOAA-20',interval=dict(start_date=str(first),end_date=str(last)),region='northern-california',bbox=dict(west=-122.5,south=38.5,east=-120,north=40.5),sha256=sha(source_csv),retrieved_at=max(retrieved),attribution_url='https://www.earthdata.nasa.gov/data/tools/firms',earliest_feature_cutoff='2025-05-01T00:00:00Z',last_label_cutoff='2025-10-31T23:59:59Z',covered_intervals=[dict(start_date=str(first),end_date=str(last))],confirmed_empty_intervals=[dict(start_date=str(d),end_date=str(d)) for d in empty]))
    _write(work/'archive-manifest.json',manifest.model_dump(mode='json'))
    _write(work/'assembly-provenance.json',dict(schema_version='real-api-csv-assembly-v1',transformation='Ordered original provider columns and rows assembled with one header and LF line endings; no measurement or observation added or changed.',original_fetches=records,assembled_sha256=sha(source_csv),empty_day_meaning='Successful full-date SP area-API response with no reported observations; not absence of fire or cloud-free coverage.'))
    prepared=prepare_archive(source_csv,manifest,Path('data/archive-imports'),policy=NormalizationPolicy(),chunk_rows=1000)
    database=Database(settings)
    try:
        imported=import_prepared(prepared,database.engine,chunk_rows=1000)
        print('2025 source import:',imported['status'],len(rows),'provider rows',flush=True)
    finally:
        database.close()
    if not sources.exists():
        export_firms_handoff(sorted(Path('data/archive-imports').glob('*/handoff.json')),[],sources)
    print('Full source collection exported:',sha(sources/'manifest.json'),flush=True)
elif stage=='backfill':
    database=Database(settings)
    try:
        output=Path('data/backfills/independent-2025-v1')
        result=run_backfill(database.engine,settings,output,load_design(Path('configs/target-dataset-2025-holdout-v1.yaml')),load_feature_spec(settings.features_config),OutageCatalog.model_validate(yaml.safe_load(Path('configs/label-outages.yaml').read_text())),resume=output.exists(),progress=lambda row:print(json.dumps({k:row[k] for k in ('completed_chunks','total_chunks','rows','eligible_rows')}),flush=True))
        print('Dataset snapshot count:',result['rows'],flush=True)
    finally:
        database.close()
    if not labels.exists():
        print(json.dumps(merge_backfill_labels(output,labels)),flush=True)
elif stage=='train':
    source_sha=sha(sources/'manifest.json')
    catalog=build_event_catalog(load_source_observations(sources,source_sha),source_sha,load_event_spec(Path('configs/event-groups-v1.yaml')))
    assert not catalog.near_misses_truncated
    lookup={g.event_id:i for i,g in enumerate(catalog.groups)}
    union=UnionFind(len(lookup))
    for pair in catalog.near_misses:
        union.join(lookup[pair.left],lookup[pair.right])
    groups={}
    for key,index in lookup.items():
        groups.setdefault(union.find(index),[]).append(key)
    review=EventReview(catalog_sha256=catalog.sha256,reviewer='Codex automated conservative partition audit',decisions={g.event_id:EventDecision(decision='accept',reason='Retain thermal proxy; transitively merge every flagged near-neighbor link before splitting. No independent incident verification claimed.') for g in catalog.groups},merge_sets=[sorted(keys) for keys in groups.values() if len(keys)>1],note='Automated technical partition audit only. All near-neighbor links merged conservatively. No final model predictions inspected; not human model-release approval.')
    review_path=work/'event-review.json'; _write(review_path,review.model_dump(mode='json'))
    if not splits.exists():
        cli('build-splits','--labels',labels,'--label-sha256',sha(labels/'manifest.json'),'--sources',sources,'--source-sha256',source_sha,'--review',review_path,'--spec','configs/splits-2025-holdout-v1.yaml','--output',splits)
    membership=json.loads((splits/'membership.json').read_text())
    assert not any(m['partition'] is not None and m['partition']!='final_test' and m['as_of']>='2024-08-15' for m in membership['membership'])
    if not training.exists():
        cli('train','--splits',splits,'--manifest-sha256',sha(splits/'manifest.json'),'--output',training)
elif stage=='development':
    if not development.exists():
        cli('evaluate','--training',training,'--manifest-sha256',sha(training/'manifest.json'),'--output',development)
elif stage=='final':
    amendment=json.loads((work/'protocol-amendment-v2.json').read_text())
    assert all(sha(Path(p))==h for p,h in amendment['files_sha256'].items()),'Frozen pre-test implementation/configuration changed'
    report=json.loads((development/'evaluation-report.json').read_text())
    assert report['status']=='development_frozen' and report['holdout_key']==amendment['holdout_key']
    cli('evaluate','--final','--prepared',development,'--manifest-sha256',sha(development/'manifest.json'),'--output',final)
elif stage=='bundle':
    if not bundle.exists():
        cli('build-inference-bundle','--evaluation',final,'--manifest-sha256',sha(final/'manifest.json'),'--scorer-version','forest-independent-2025-v2','--output',bundle)
else:
    raise ValueError('Unknown experiment stage')
