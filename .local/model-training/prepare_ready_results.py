"""Prepare actual historical weather and checksummed replay results without model bypasses."""
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml
from sqlalchemy import select

from firewatch.config import get_settings
from firewatch.db.models import IngestionBatch, Region, Run
from firewatch.db.session import Database
from firewatch.pipeline.replay import replay_scenario
from firewatch.pipeline.replay_bundle import export_bundle
from firewatch.processing.features import load_feature_spec
from firewatch.repositories.features import _request
from firewatch.repositories.snapshots import build_run_snapshots, policy_for_snapshots
from firewatch.repositories.weather import get_weather
from firewatch.schemas.common import Product, RunMode, Sensor
from firewatch.schemas.operator import ReplayScenario
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import OpenMeteoClient, WeatherRequest
from firewatch.sources.weather_budget import WeatherBudget

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'pyproject.toml').is_file())
os.chdir(ROOT)
directory = ROOT / '.local/readiness'
directory.mkdir(exist_ok=True)
catalog_path = directory / 'replays.yaml'
bundle_root = ROOT / 'data/replays-ready-v1'
os.environ['REPLAYS_CONFIG'] = str(catalog_path)
os.environ['REPLAY_BUNDLE_DIR'] = str(bundle_root)
get_settings.cache_clear()
settings = get_settings()
database = Database(settings)
features = load_feature_spec(settings.features_config)
definitions = [
    dict(id='norcal-ready-active-v1', name='Real northern California hotspots with recovered weather, 25 July 2024', cutoff='2024-07-25T22:00:00+00:00', kind='active'),
    dict(id='norcal-ready-quiet-v1', name='Real northern California quiet observation window, 6 May 2024', cutoff='2024-05-06T12:00:00+00:00', kind='quiet'),
]

def prepare(definition, session, clock):
    cutoff = datetime.fromisoformat(definition['cutoff'])
    area = session.scalar(select(Region).where(Region.name == 'northern-california'))
    assert area is not None
    policy = policy_for_snapshots(session, area.id, cutoff, {Sensor.NOAA20: Product.NOAA20_SP}, clock)
    run = Run(id=uuid5(NAMESPACE_URL, 'ready-prepare:' + definition['id']), region_id=area.id,
              mode=RunMode.REPLAY, as_of=cutoff, status='running', started_at=clock,
              config_hash=digest(features.model_dump(mode='json')), run_key='ready-prepare:' + definition['id'],
              scorer_version='baseline-v1', feature_version='features-v1', completeness='unavailable', stage_status={})
    session.add(run)
    session.flush()
    snapshots = build_run_snapshots(session, run.id, policy)
    return run, snapshots, policy

try:
    if sys.argv[1] == 'weather':
        clock = datetime.now(UTC)
        requests = {}
        with database.session() as session:
            run, snapshots, policy = prepare(definitions[0], session, clock)
            for item in snapshots.snapshots:
                request = _request(item.snapshot, features)
                requests[request.cells[0].key] = request
            print('Real active snapshots:', len(snapshots.snapshots), 'weather cells:', len(requests), flush=True)
        first = next(iter(requests.values()))
        cells = [request.cells[0] for request in requests.values()]
        combined = WeatherRequest(cells=cells, start=first.start, end=first.end, product=first.product,
                                  model=first.model, source_version=first.source_version, mode=first.mode)
        client = OpenMeteoClient(settings.raw_data_dir / 'weather', WeatherBudget(settings.raw_data_dir.parent / 'weather-budget.sqlite'))
        try:
            series = get_weather(database.engine, combined, client=client)
            print('Real provider weather rows cached:', len(series.records), flush=True)
            (directory / 'weather-recovery.json').write_text(json.dumps(dict(
                recovered_at=datetime.now(UTC).isoformat(), data_kind='real', provider='Open-Meteo',
                product=combined.product, model=combined.model, cells=len(cells), rows=len(series.records),
                start=combined.start.isoformat(), end=combined.end.isoformat(),
            ), indent=2) + '\n')
        finally:
            client.close()
    elif sys.argv[1] == 'publish':
        clock = datetime.now(UTC)
        entries = []
        if catalog_path.exists():
            entries = yaml.safe_load(catalog_path.read_text())['scenarios']
        else:
            for definition in definitions:
                with database.session() as session:
                    run, snapshots, policy = prepare(definition, session, clock)
                    assets = sorted(session.scalars(select(IngestionBatch.asset_id).where(IngestionBatch.id.in_(policy.batch_ids))), key=str)
                    scenario = ReplayScenario(id=definition['id'], name=definition['name'], region_name='northern-california',
                                              window_start=run.as_of-timedelta(hours=24), window_end=run.as_of,
                                              scorer_version='baseline-v1', feature_version='features-v1', asset_ids=assets)
                    checksum = export_bundle(session, run, snapshots, policy, features, scenario, bundle_root / scenario.id,
                        prepared_at=clock, data_kind='real', scenario_kind=definition['kind'],
                        coverage_note='Actual NOAA-20 SP archive observations and recovered Open-Meteo GFS historical weather where observations require it. Climate baselines unavailable and explicitly flagged. Retrospective research replay; no live or strict operational backtest claim.',
                        secrets=[settings.admin_api_token.get_secret_value(), settings.firms_map_key.get_secret_value() if settings.firms_map_key else ''])
                    entries.append(scenario.model_copy(update={'bundle_sha256':checksum}).model_dump(mode='json'))
            catalog_path.write_text(yaml.safe_dump(dict(schema_version='replays-v1',scenarios=entries),sort_keys=False),encoding='utf-8')
        results=[]
        for entry in entries:
            result = replay_scenario(database.engine, settings, entry['id'])
            results.append(result)
            print(json.dumps(result),flush=True)
        (directory / 'published-results.json').write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
    else:
        raise ValueError('Use weather or publish')
finally:
    database.close()
