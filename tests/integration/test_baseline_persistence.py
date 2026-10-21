"""PostgreSQL baseline publication, resume and integrity acceptance with synthetic POWER data."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from conftest import migrate
from power_factory import NOW, batches, client, request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firewatch.db.models import ClimateBaselineRecord, SourceAsset
from firewatch.processing.baselines import BaselineSpec
from firewatch.repositories.baselines import (
    build_baselines,
    cached_power,
    get_baseline,
    publish_baselines,
    save_power_batch,
)
from firewatch.sources.weather_budget import WeatherError

pytestmark = pytest.mark.integration


@pytest.fixture
def climate_engine(empty_engine):
    migrate(empty_engine)
    return empty_engine


def test_real_database_build_dedup_and_offline(climate_engine, tmp_path):
    provider, calls = client(tmp_path)
    cell = request().cell
    spec = BaselineSpec()
    first = build_baselines(
        climate_engine, [cell, cell], spec, tmp_path / "baseline", client=provider
    )
    second = build_baselines(
        climate_engine, [cell], spec, tmp_path / "baseline", offline=True, retrieval_cutoff=NOW
    )
    assert first == second and len(first) == 12 and len(calls) == 10
    with Session(climate_engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceAsset)) == 11
        assert session.scalar(select(func.count()).select_from(ClimateBaselineRecord)) == 12
        assert get_baseline(session, cell, 7, spec, retrieval_cutoff=NOW) == first[6]
        assert (
            get_baseline(session, cell, 7, spec, retrieval_cutoff=NOW - timedelta(seconds=1))
            is None
        )


def test_failure_retains_annual_checkpoints_and_resumes(climate_engine, tmp_path):
    provider, calls = client(tmp_path, fail_year=2012)
    cell = request().cell
    spec = BaselineSpec()
    with pytest.raises(WeatherError):
        build_baselines(climate_engine, [cell], spec, tmp_path / "baseline", client=provider)
    with Session(climate_engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceAsset)) == 2
        assert session.scalar(select(func.count()).select_from(ClimateBaselineRecord)) == 0
    resumed, new_calls = client(tmp_path)
    rows = build_baselines(climate_engine, [cell], spec, tmp_path / "baseline", client=resumed)
    assert len(rows) == 12 and len(calls) == 5 and len(new_calls) == 8


def test_missing_offline_sources_never_fetch(climate_engine, tmp_path):
    provider, calls = client(tmp_path)
    with pytest.raises(WeatherError):
        build_baselines(
            climate_engine,
            [request().cell],
            BaselineSpec(),
            tmp_path / "out",
            client=provider,
            offline=True,
        )
    assert calls == []


def test_batch_row_and_raw_tampering_rejected(climate_engine, tmp_path):
    provider, _ = client(tmp_path)
    batch = provider.fetch(request())
    modified = deepcopy(batch)
    modified.days[0].temperature_c = 500
    with pytest.raises(WeatherError), Session(climate_engine) as session, session.begin():
        save_power_batch(session, modified)
    with Session(climate_engine) as session, session.begin():
        save_power_batch(session, batch)
        save_power_batch(session, batch)
    Path(batch.asset.storage_path).write_text("tampered")
    with pytest.raises(WeatherError), Session(climate_engine) as session:
        cached_power(session, request(), NOW)


def test_publish_rollback_and_fixed_version_conflict(climate_engine, tmp_path):
    raw = batches(tmp_path)
    with Session(climate_engine) as session, session.begin():
        for batch in raw:
            save_power_batch(session, batch)
    with pytest.raises(RuntimeError), Session(climate_engine) as session, session.begin():
        publish_baselines(session, raw, BaselineSpec(), tmp_path / "out")
        raise RuntimeError("simulated stage failure")
    with Session(climate_engine) as session:
        assert session.scalar(select(func.count()).select_from(ClimateBaselineRecord)) == 0
    with Session(climate_engine) as session, session.begin():
        first = publish_baselines(session, raw, BaselineSpec(), tmp_path / "out")
        second = publish_baselines(session, raw, BaselineSpec(), tmp_path / "out")
        assert first == second
    with pytest.raises(WeatherError), Session(climate_engine) as session, session.begin():
        publish_baselines(session, raw, BaselineSpec(min_valid_fraction=0.8), tmp_path / "out")
    with Session(climate_engine) as session:
        assert session.scalar(select(func.count()).select_from(ClimateBaselineRecord)) == 12


def test_definition_drift_and_manifest_tampering_fail_closed(climate_engine, tmp_path):
    provider, _ = client(tmp_path)
    spec = BaselineSpec()
    cell = request().cell
    rows = build_baselines(climate_engine, [cell], spec, tmp_path / "out", client=provider)
    with pytest.raises(WeatherError):
        build_baselines(
            climate_engine,
            [cell],
            BaselineSpec(min_valid_fraction=0.8),
            tmp_path / "out",
            offline=True,
            retrieval_cutoff=NOW,
        )
    with Session(climate_engine) as session:
        asset = session.get(SourceAsset, rows[0].asset_id)
        Path(asset.storage_path).write_text("tampered")
    with pytest.raises(WeatherError):
        build_baselines(
            climate_engine, [cell], spec, tmp_path / "out", offline=True, retrieval_cutoff=NOW
        )


def test_frozen_cutoff_and_missing_cell_lookup(climate_engine, tmp_path):
    provider, calls = client(tmp_path)
    with pytest.raises(WeatherError):
        build_baselines(
            climate_engine,
            [request().cell],
            BaselineSpec(),
            tmp_path / "out",
            client=provider,
            retrieval_cutoff=NOW - timedelta(days=1),
        )
    assert calls == []
    with Session(climate_engine) as session:
        assert (
            get_baseline(session, request().cell, 1, BaselineSpec(), retrieval_cutoff=NOW) is None
        )
        with pytest.raises(ValueError):
            get_baseline(session, request().cell, 13, BaselineSpec(), retrieval_cutoff=NOW)
