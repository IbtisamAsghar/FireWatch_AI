"""Real PostgreSQL feature pinning, late-arrival, rollback and integrity acceptance."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from conftest import migrate
from power_factory import NOW
from power_factory import client as power_client
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_snapshot_persistence import ingest, setup_sources
from weather_factory import payload

from firewatch.db.models import FeatureSetRecord, Run, WeatherRecord
from firewatch.processing.baselines import BaselineSpec
from firewatch.processing.features import FeatureSpec, model_inputs
from firewatch.repositories.baselines import publish_baselines, save_power_batch
from firewatch.repositories.features import build_run_features, fork_feature_run
from firewatch.repositories.snapshots import build_run_snapshots
from firewatch.repositories.weather import save_weather
from firewatch.schemas.common import RunMode, Stage
from firewatch.sources.open_meteo import OpenMeteoClient, WeatherRequest, weather_cell
from firewatch.sources.power import PowerRequest, climate_cell
from firewatch.sources.weather_budget import WeatherBudget

pytestmark = pytest.mark.integration


def warm(session, tmp_path, snapshots, *, baseline=True, partial=False, temperature=10):
    snapshot = snapshots.snapshots[0].snapshot
    cell = weather_cell(snapshot.latitude, snapshot.longitude)
    end = snapshot.as_of.replace(minute=0, second=0, microsecond=0)
    request = WeatherRequest(
        cells=[cell], start=end - timedelta(hours=10 if partial else 23), end=end
    )
    data = payload(request)
    data["hourly"]["temperature_2m"] = [temperature] * len(request.hours())
    provider = OpenMeteoClient(
        tmp_path / "weather",
        WeatherBudget(tmp_path / "weather-budget"),
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data))
        ),
        now=lambda: NOW,
        sleep=lambda _: None,
    )
    weather = provider.fetch(request)
    save_weather(session, weather)
    if baseline:
        cc = climate_cell(cell.latitude, cell.longitude)
        power, _ = power_client(tmp_path / "power")
        batches = [power.fetch(PowerRequest(cell=cc, year=y)) for y in range(2010, 2020)]
        for batch in batches:
            save_power_batch(session, batch)
        publish_baselines(session, batches, BaselineSpec(), tmp_path / "baselines")
    return weather


@pytest.fixture
def stage(empty_engine, tmp_path):
    migrate(empty_engine)
    with Session(empty_engine) as session, session.begin():
        area, run, policy = setup_sources(session, tmp_path)
        run.mode = RunMode.REPLAY
        session.flush()
        snapshots = build_run_snapshots(session, run.id, policy)
        yield session, area, run, policy, snapshots


def test_complete_feature_stage_persists_and_retries(stage, tmp_path):
    session, _, run, _, snapshots = stage
    warm(session, tmp_path, snapshots)
    first = build_run_features(session, run.id, retrieval_cutoff=NOW)
    second = build_run_features(session, run.id)
    assert first == second and len(first) == 1
    assert (
        first[0].values["temperature_c"] == 10 and first[0].values["temperature_departure_c"] == 0
    )
    assert first[0].values["brightness_max_k"] == 330.2
    assert first[0].values["hours_since_first_seen_24h"] == 23
    assert session.scalar(select(func.count()).select_from(FeatureSetRecord)) == 1
    assert run.stage_status[Stage.FEATURES.value] == "succeeded"
    assert run.stage_status[Stage.ENRICHMENT.value] == "succeeded" and run.status == "running"
    assert len(model_inputs(first[0])) == 31


def test_missing_enrichment_is_frozen_and_fork_can_use_late_arrivals(stage, tmp_path):
    session, _, run, _, snapshots = stage
    first = build_run_features(session, run.id, retrieval_cutoff=NOW)
    assert first[0].values["temperature_c"] is None
    warm(session, tmp_path, snapshots)
    assert build_run_features(session, run.id) == first
    fork = fork_feature_run(session, run.id, FeatureSpec())
    newer = build_run_features(session, fork, retrieval_cutoff=NOW)
    assert newer[0].values["temperature_c"] == 10
    assert newer[0].run_id != first[0].run_id and newer[0].snapshot_id != first[0].snapshot_id
    assert build_run_features(session, run.id) == first


def test_new_weather_revisions_and_corrected_detections_cannot_change_saved_features(
    stage, tmp_path
):
    session, area, run, policy, snapshots = stage
    warm(session, tmp_path, snapshots)
    first = build_run_features(session, run.id, retrieval_cutoff=NOW)
    warm(session, tmp_path, snapshots, baseline=False, temperature=80)
    from firms_factory import NOW as FIRMS_NOW
    from firms_factory import ROW

    ingest(
        session,
        tmp_path,
        area,
        [ROW | {"acq_time": "1100", "frp": "1000"}],
        retrieved=FIRMS_NOW + timedelta(hours=1),
    )
    assert build_run_features(session, run.id) == first
    assert build_run_snapshots(session, run.id, policy) == snapshots


def test_partial_cache_preserves_weather_but_withholds_rainfall(stage, tmp_path):
    session, _, run, _, snapshots = stage
    warm(session, tmp_path, snapshots, baseline=False, partial=True)
    feature = build_run_features(session, run.id, retrieval_cutoff=NOW)[0]
    assert feature.values["temperature_c"] == 10
    assert feature.values["precipitation_24h_mm"] is None
    assert "weather_cache_partial_interval" in feature.lineage["quality_flags"]


def test_feature_row_and_config_tampering_fail_closed(stage, tmp_path):
    session, _, run, _, snapshots = stage
    warm(session, tmp_path, snapshots)
    build_run_features(session, run.id, retrieval_cutoff=NOW)
    with pytest.raises(ValueError, match="configuration"):
        build_run_features(session, run.id, FeatureSpec(weather_max_age_hours=4))
    row = session.scalar(select(FeatureSetRecord))
    changed = dict(row.values)
    changed["temperature_c"] = 100
    row.values = changed
    session.flush()
    with pytest.raises(ValueError, match="integrity"):
        build_run_features(session, run.id)


def test_raw_weather_and_stage_pin_tampering_fail_closed(stage, tmp_path):
    session, _, run, _, snapshots = stage
    weather = warm(session, tmp_path, snapshots, baseline=False)
    build_run_features(session, run.id, retrieval_cutoff=NOW)
    from pathlib import Path

    Path(weather.asset.storage_path).write_text("tampered")
    with pytest.raises(ValueError, match="checksum"):
        build_run_features(session, run.id)
    changed = deepcopy(run.input_asset_manifest)
    changed["features"]["pins"]["weather"] = {}
    run.input_asset_manifest = changed
    session.flush()
    with pytest.raises(ValueError, match="stage"):
        build_run_features(session, run.id)


def test_feature_transaction_rollback(stage, tmp_path):
    session, _, run, _, snapshots = stage
    warm(session, tmp_path, snapshots, baseline=False)
    identifier = run.id
    with pytest.raises(RuntimeError), session.begin_nested():
        expected = build_run_features(session, identifier, retrieval_cutoff=NOW)
        raise RuntimeError("simulated feature stage interruption")
    session.expire_all()
    assert "features" not in session.get(Run, identifier).input_asset_manifest
    assert session.scalar(select(func.count()).select_from(FeatureSetRecord)) == 0
    assert build_run_features(session, identifier, retrieval_cutoff=NOW) == expected


def test_frozen_availability_and_future_row_insert(stage, tmp_path):
    session, _, run, _, snapshots = stage
    weather = warm(session, tmp_path, snapshots, baseline=False)
    with pytest.raises(ValueError, match="availability"):
        build_run_features(session, run.id, retrieval_cutoff=snapshots.as_of)
    first = build_run_features(session, run.id, retrieval_cutoff=NOW)
    # A later stored valid-time row does not enter the pinned 24-hour enrichment slice.
    row = deepcopy(weather.series.records[-1])
    row.valid_at += timedelta(hours=1)
    row.temperature_c = 99
    session.add(WeatherRecord(**row.model_dump()))
    session.flush()
    assert build_run_features(session, run.id) == first
    with pytest.raises(ValueError, match="repinned"):
        build_run_features(session, run.id, retrieval_cutoff=NOW + timedelta(hours=1))


def test_empty_snapshot_run_has_explicit_empty_feature_stage(empty_engine, tmp_path):
    migrate(empty_engine)
    with Session(empty_engine) as session, session.begin():
        _, run, policy = setup_sources(session, tmp_path, empty=True)
        build_run_snapshots(session, run.id, policy)
        assert build_run_features(session, run.id, retrieval_cutoff=NOW) == []
        assert build_run_features(session, run.id) == []
        assert run.input_asset_manifest["features"]["feature_hashes"] == {}


def test_concurrent_feature_retries_write_one_set(empty_engine, tmp_path):
    migrate(empty_engine)
    with Session(empty_engine) as session, session.begin():
        _, run, policy = setup_sources(session, tmp_path)
        run.mode = RunMode.REPLAY
        session.flush()
        snapshots = build_run_snapshots(session, run.id, policy)
        warm(session, tmp_path, snapshots, baseline=False)
        identifier = run.id

    def execute(_):
        with Session(empty_engine) as session, session.begin():
            return build_run_features(session, identifier, retrieval_cutoff=NOW)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(execute, range(2)))
    assert first == second
    with Session(empty_engine) as session:
        assert session.scalar(select(func.count()).select_from(FeatureSetRecord)) == 1


def test_pinned_normalized_weather_row_tampering_is_rejected(stage, tmp_path):
    session, _, run, _, snapshots = stage
    warm(session, tmp_path, snapshots, baseline=False)
    build_run_features(session, run.id, retrieval_cutoff=NOW)
    row = session.scalar(select(WeatherRecord).order_by(WeatherRecord.valid_at.desc()))
    row.temperature_c = 90
    session.flush()
    with pytest.raises(ValueError, match="normalized weather"):
        build_run_features(session, run.id)
