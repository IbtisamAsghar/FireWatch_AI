"""Prepare real enriched inputs for the candidate; execution still requires approval."""
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
from firewatch.repositories.baselines import build_baselines
from firewatch.repositories.features import _request
from firewatch.repositories.snapshots import build_run_snapshots, policy_for_snapshots
from firewatch.repositories.weather import get_weather
from firewatch.schemas.common import Product, RunMode, Sensor
from firewatch.schemas.operator import ReplayScenario
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import OpenMeteoClient, WeatherRequest
from firewatch.sources.power import PowerClient, climate_cell
from firewatch.sources.weather_budget import WeatherBudget

ROOT=next(p for p in Path(__file__).resolve().parents if (p/'pyproject.toml').is_file())
os.chdir(ROOT)
settings=get_settings()
database=Database(settings)
features=load_feature_spec(settings.features_config)
directory=ROOT/'.local/readiness'
identifier='norcal-ml-weather-v1'
version='forest-independent-2025-v2'
baseline = sys.argv[1] == 'baseline-publish'
if baseline:
    identifier='norcal-weather-climate-v1'
    version='baseline-v1'
cutoff=datetime(2024,7,26,tzinfo=UTC)
clock=datetime.now(UTC)

def prepared(session):
    area=session.scalar(select(Region).where(Region.name=='northern-california'))
    policy=policy_for_snapshots(session,area.id,cutoff,{Sensor.NOAA20:Product.NOAA20_SP},clock)
    run=Run(id=uuid5(NAMESPACE_URL,'prepare:'+identifier),region_id=area.id,mode=RunMode.REPLAY,as_of=cutoff,status='running',started_at=clock,config_hash=digest(features.model_dump(mode='json')),run_key='prepare:'+identifier,scorer_version=version,feature_version='features-v1',completeness='unavailable',stage_status={})
    session.add(run); session.flush()
    snapshots=build_run_snapshots(session,run.id,policy)
    return run,snapshots,policy

try:
    with database.session() as session:
        run,snapshots,policy=prepared(session)
        requests={request.cells[0].key:request for request in [_request(item.snapshot,features) for item in snapshots.snapshots]}
    first=next(iter(requests.values()))
    climate={climate_cell(r.cells[0].latitude,r.cells[0].longitude).key:climate_cell(r.cells[0].latitude,r.cells[0].longitude) for r in requests.values()}
    print('Replay:',identifier,'snapshots:',len(snapshots.snapshots),'weather cells:',len(requests),'climate cells:',len(climate),flush=True)
    stage=sys.argv[1]
    if stage=='weather':
        combined=WeatherRequest(cells=[r.cells[0] for r in requests.values()],start=first.start,end=first.end,product=first.product,model=first.model,source_version=first.source_version,mode=first.mode)
        client=OpenMeteoClient(settings.raw_data_dir/'weather',WeatherBudget(settings.raw_data_dir.parent/'weather-budget.sqlite'))
        try:
            series=get_weather(database.engine,combined,client=client)
            print('Actual weather rows cached:',len(series.records),flush=True)
        finally:
            client.close()
    elif stage=='climate':
        client=PowerClient(settings.raw_data_dir/'power',WeatherBudget(settings.raw_data_dir.parent/'power-budget.sqlite'))
        try:
            for cell in climate.values():
                rows=build_baselines(database.engine,[cell],features.climate,settings.raw_data_dir.parent/'climate-baselines',client=client)
                print('Real NASA POWER baselines cached:',cell.key,len(rows),flush=True)
        finally:
            client.close()
    elif stage in {'bundle', 'baseline-publish'}:
        with database.session() as session:
            run,snapshots,policy=prepared(session)
            assets=sorted(session.scalars(select(IngestionBatch.asset_id).where(IngestionBatch.id.in_(policy.batch_ids))),key=str)
            scenario=ReplayScenario(id=identifier,name='Real northern California hotspots, weather and climate context, 26 July 2024',region_name='northern-california',window_start=cutoff-timedelta(hours=24),window_end=cutoff,scorer_version=version,feature_version='features-v1',asset_ids=assets)
            checksum=export_bundle(session,run,snapshots,policy,features,scenario,settings.replay_bundle_dir/identifier,prepared_at=clock,data_kind='real',scenario_kind='active',coverage_note='Real NOAA-20 SP archive observations, recovered GFS historical hourly weather and NASA POWER 2010-2019 climate context. Historical research replay. Original wide-cluster warnings are preserved.',secrets=[settings.admin_api_token.get_secret_value(),settings.firms_map_key.get_secret_value()])
            (directory/'ml-scenario.yaml').write_text(yaml.safe_dump(scenario.model_copy(update={'bundle_sha256':checksum}).model_dump(mode='json'),sort_keys=False),encoding='utf-8')
            print('Frozen ML replay inputs:',checksum,flush=True)
        if baseline:
            catalog=yaml.safe_load(settings.replays_config.read_text())
            catalog['scenarios'].append(scenario.model_copy(update={'bundle_sha256':checksum}).model_dump(mode='json'))
            settings.replays_config.write_text(yaml.safe_dump(catalog,sort_keys=False),encoding='utf-8')
            result=replay_scenario(database.engine,settings,identifier)
            (directory/'enriched-baseline-result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(result),flush=True)
    elif stage=='publish':
        assert settings.active_scorer=='registry','Do not publish until the reviewed model is registry-active'
        scenario=yaml.safe_load((directory/'ml-scenario.yaml').read_text())
        catalog=yaml.safe_load(settings.replays_config.read_text())
        if not any(s['id']==identifier for s in catalog['scenarios']):
            catalog['scenarios'].append(scenario)
            settings.replays_config.write_text(yaml.safe_dump(catalog,sort_keys=False),encoding='utf-8')
        result=replay_scenario(database.engine,settings,identifier)
        (directory/'ml-published-result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result),flush=True)
    else:
        raise ValueError('Unknown stage')
finally:
    database.close()
