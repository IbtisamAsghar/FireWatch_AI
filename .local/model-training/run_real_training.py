"""Rebuild real seasonal inputs and fit a development candidate; no promotion."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / 'pyproject.toml').is_file())
os.chdir(ROOT)
state = json.loads((ROOT / '.local/model-training/database.json').read_text())
os.environ['DATABASE_URL'] = f"postgresql+psycopg://firewatch:{state['app_password']}@127.0.0.1:{state['port']}/firewatch"

def cli(*args):
    print('Running:', ' '.join(args), flush=True)
    result = subprocess.run([str(ROOT / '.venv/Scripts/firewatch.exe'), *args], cwd=ROOT)
    if result.returncode:
        raise RuntimeError('Stage failed: ' + args[0])

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

stage = sys.argv[1]
if stage == 'sources':
    cli('seed-regions')
    for year in (2022, 2024):
        cli('import-firms', '--path', f'data/recovered-archives/season-{year}.csv', '--manifest', f'docs/manifests/season-{year}-archive.json', '--output', 'data/archive-imports', '--chunk-rows', '1000')
    from firewatch.sources.firms_handoff import export_firms_handoff
    destination = Path('data/sources/recovered-seasons-v1')
    if not destination.exists():
        result = export_firms_handoff(sorted(Path('data/archive-imports').glob('*/handoff.json')), [], destination)
        print('Source export complete:', result.get('schema_version'), flush=True)
    print('Source SHA256:', sha(destination / 'manifest.json'), flush=True)
elif stage == 'backfill':
    args = ['backfill-training-data', '--output', 'data/backfills/recovered-seasons-v1']
    if Path(args[2]).exists():
        args.append('--resume')
    cli(*args)
    if not Path('data/labels/recovered-seasons-v1').exists():
        cli('merge-backfill-labels', '--path', 'data/backfills/recovered-seasons-v1', '--output', 'data/labels/recovered-seasons-v1')
elif stage == 'train':
    from firewatch.ml.events import EventDecision, EventReview, UnionFind, build_event_catalog
    from firewatch.ml.split_package import load_event_spec, load_source_observations, _write
    sources = Path('data/sources/recovered-seasons-v1')
    labels = Path('data/labels/recovered-seasons-v1')
    source_sha = sha(sources / 'manifest.json')
    catalog = build_event_catalog(load_source_observations(sources, source_sha), source_sha, load_event_spec(Path('configs/event-groups-v1.yaml')))
    assert not catalog.near_misses_truncated, 'Cannot review a truncated list'
    lookup = {group.event_id: i for i, group in enumerate(catalog.groups)}
    union = UnionFind(len(lookup))
    for pair in catalog.near_misses:
        union.join(lookup[pair.left], lookup[pair.right])
    parts = {}
    for key, index in lookup.items():
        parts.setdefault(union.find(index), []).append(key)
    review = EventReview(
        catalog_sha256=catalog.sha256,
        reviewer='Codex automated conservative partition audit',
        decisions={g.event_id: EventDecision(decision='accept', reason='Retain satellite activity proxy; merge all flagged near-neighbor links before partitioning. No independent incident validity claimed.') for g in catalog.groups},
        merge_sets=[sorted(keys) for keys in parts.values() if len(keys) > 1],
        note='Automated technical partition review: every reported near-miss pair is transitively merged, never asserted independent. Raw group geometry and full-source membership are preserved. These groups are satellite proxies, not verified fires. This is not model release approval or human review.',
    )
    review_path = Path('data/sources/recovered-event-review.json')
    _write(review_path, review.model_dump(mode='json'))
    print('Partition audit:', len(catalog.observations), 'observations;', len(catalog.groups), 'raw groups;', len(parts), 'conservative groups;', len(catalog.near_misses), 'near-neighbor links merged', flush=True)
    splits = Path('data/splits/recovered-seasons-v1')
    if not splits.exists():
        cli('build-splits', '--labels', str(labels), '--label-sha256', sha(labels / 'manifest.json'), '--sources', str(sources), '--source-sha256', source_sha, '--review', str(review_path), '--output', str(splits))
    output = Path('data/models/recovered-seasons-v1')
    if not output.exists():
        cli('train', '--splits', str(splits), '--manifest-sha256', sha(splits / 'manifest.json'), '--output', str(output))
    print('Trained package:', output, flush=True)
    print('Model SHA256:', sha(output / 'model.json'), flush=True)
elif stage == 'development':
    training = Path('data/models/recovered-seasons-v1')
    output = Path('data/evaluations/recovered-development-v1')
    if not output.exists():
        cli('evaluate', '--training', str(training), '--manifest-sha256', sha(training / 'manifest.json'), '--output', str(output))
    bundle = Path('data/inference/recovered-ranking-v1')
    if not bundle.exists():
        cli('build-inference-bundle', '--evaluation', str(output), '--manifest-sha256', sha(output / 'manifest.json'), '--scorer-version', 'forest-recovered-v1', '--output', str(bundle))
    print('Development-only runtime bundle:', bundle, flush=True)
else:
    raise ValueError('Unknown stage')
