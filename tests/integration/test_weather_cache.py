"""Real PostgreSQL cache tests with isolated schemas and synthetic weather."""

from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from weather_factory import NOW, T, payload, request

from firewatch.db.models import SourceAsset, WeatherRecord
from firewatch.processing.weather import weather_at_cutoff
from firewatch.repositories.weather import cached_weather, get_weather, save_weather
from firewatch.sources.open_meteo import OpenMeteoClient, WeatherRequest
from firewatch.sources.weather_budget import WeatherBudget, WeatherError

pytestmark = pytest.mark.integration


def make_client(tmp_path, req, *, now=NOW, temperature=1):
    calls = []

    def handler(r):
        calls.append(r)
        data = payload(req)
        data["hourly"]["temperature_2m"] = [temperature] * len(req.hours())
        return httpx.Response(200, json=data)

    client = OpenMeteoClient(
        tmp_path / "raw",
        WeatherBudget(tmp_path / "budget.sqlite"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: now,
        sleep=lambda _: None,
    )
    return client, calls


def test_cache_roundtrip_offline_and_idempotence(schema_engine, tmp_path):
    req = request(source_version="roundtrip")
    client, calls = make_client(tmp_path, req)
    batch = client.fetch(req)
    with Session(schema_engine) as session, session.begin():
        save_weather(session, batch)
        save_weather(session, batch)
        assert (
            session.scalar(
                select(func.count())
                .select_from(WeatherRecord)
                .where(WeatherRecord.asset_id == batch.asset_id)
            )
            == 25
        )
    cached = get_weather(schema_engine, req, retrieval_cutoff=NOW, offline=True)
    assert cached == batch.series
    assert len(calls) == 1
    assert weather_at_cutoff(cached, req.end).precipitation_24h_mm == 24


def test_fetch_then_cached_with_no_client(schema_engine, tmp_path):
    req = request(source_version="service")
    client, calls = make_client(tmp_path, req)
    first = get_weather(schema_engine, req, client=client)
    second = get_weather(schema_engine, req, offline=True, retrieval_cutoff=NOW)
    assert first == second and len(calls) == 1


def test_frozen_retrieval_cutoff_and_revisions(schema_engine, tmp_path):
    req = request(source_version="revision")
    client, _ = make_client(tmp_path, req)
    first = client.fetch(req)
    client, _ = make_client(tmp_path, req, now=NOW + timedelta(hours=1), temperature=30)
    second = client.fetch(req)
    with Session(schema_engine) as session, session.begin():
        save_weather(session, first)
        save_weather(session, second)
    old = get_weather(schema_engine, req, retrieval_cutoff=NOW, offline=True)
    new = get_weather(schema_engine, req, retrieval_cutoff=NOW + timedelta(hours=1), offline=True)
    assert old.records[0].temperature_c == 1
    assert new.records[0].temperature_c == 30
    assert old.records[0].source_version != new.records[0].source_version
    with pytest.raises(WeatherError):
        get_weather(schema_engine, req, retrieval_cutoff=NOW - timedelta(seconds=1), offline=True)


def test_cache_product_model_namespace_and_interval_isolation(schema_engine, tmp_path):
    req = request(source_version="isolation")
    client, _ = make_client(tmp_path, req)
    get_weather(schema_engine, req, client=client)
    for updates in [
        dict(product="reanalysis", model="era5"),
        dict(source_version="other"),
        dict(end=req.end + timedelta(hours=1)),
    ]:
        other = WeatherRequest.model_validate(req.model_dump() | updates)
        with pytest.raises(WeatherError):
            get_weather(schema_engine, other, offline=True, retrieval_cutoff=NOW)


def test_live_cache_expiry(schema_engine, tmp_path):
    req = request(
        start=NOW - timedelta(hours=24),
        end=NOW,
        product="live",
        mode="live",
        source_version="live-expiry",
    )
    client, _ = make_client(tmp_path, req)
    get_weather(schema_engine, req, client=client)
    with pytest.raises(WeatherError):
        get_weather(schema_engine, req, retrieval_cutoff=NOW + timedelta(hours=2), offline=True)


def test_raw_tampering_and_row_tampering_roll_back(schema_engine, tmp_path):
    req = request(source_version="tamper")
    client, _ = make_client(tmp_path, req)
    batch = client.fetch(req)
    changed = deepcopy(batch)
    changed.series.records[0].temperature_c = 100
    with pytest.raises(WeatherError), Session(schema_engine) as session, session.begin():
        save_weather(session, changed)
    from pathlib import Path

    Path(batch.asset.storage_path).write_text("tampered")
    with pytest.raises(WeatherError), Session(schema_engine) as session, session.begin():
        save_weather(session, batch)
    with Session(schema_engine) as session:
        assert session.get(SourceAsset, batch.asset_id) is None


def test_partial_interval_not_stitched_across_source_revisions(schema_engine, tmp_path):
    req = request(source_version="partial")
    for start, end in [(T, T + timedelta(hours=12)), (T + timedelta(hours=13), req.end)]:
        short = WeatherRequest.model_validate(req.model_dump() | dict(start=start, end=end))
        client, _ = make_client(tmp_path, short)
        get_weather(schema_engine, short, client=client)
    with pytest.raises(WeatherError):
        get_weather(schema_engine, req, retrieval_cutoff=NOW, offline=True)


def test_offline_miss_never_fetches(schema_engine, tmp_path):
    req = request(source_version="offline-miss")
    client, calls = make_client(tmp_path, req)
    with pytest.raises(WeatherError):
        get_weather(schema_engine, req, client=client, offline=True)
    assert calls == []


def test_transaction_rollback_preserves_no_partial_cache(schema_engine, tmp_path):
    req = request(source_version="rollback")
    client, _ = make_client(tmp_path, req)
    batch = client.fetch(req)
    with pytest.raises(RuntimeError), Session(schema_engine) as session, session.begin():
        save_weather(session, batch)
        raise RuntimeError("simulated downstream failure")
    with Session(schema_engine) as session:
        assert session.get(SourceAsset, batch.asset_id) is None
        assert cached_weather(session, req, NOW) is None
