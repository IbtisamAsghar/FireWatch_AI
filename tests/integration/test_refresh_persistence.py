"""Real PostgreSQL acceptance for bounded FIRMS refresh progress and source status."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from conftest import migrate
from refresh_factory import NOW, FakeRefreshClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from firewatch.db.models import Detection, DetectionSource, IngestionBatch, Region, Run, SourceAsset
from firewatch.pipeline.firms_refresh import refresh_firms
from firewatch.repositories.refresh import load_refresh
from firewatch.schemas.common import JobStatus, Product
from firewatch.schemas.firms_refresh import RefreshSpec
from firewatch.sources.firms import FirmsError

pytestmark = pytest.mark.integration


@pytest.fixture
def refresh_engine(empty_engine):
    migrate(empty_engine)
    add_region(empty_engine)
    return empty_engine


def add_region(engine, enabled=True):
    with Session(engine) as session, session.begin():
        area = Region(name="refresh-region", west=70, south=33, east=74, north=36, enabled=enabled)
        session.add(area)


def spec(**changes):
    return RefreshSpec.model_validate(dict(region="refresh-region", as_of=NOW) | changes)


def execute(engine, client, tmp_path, body=None, **kwargs):
    return refresh_firms(
        engine, client, body or spec(), output=tmp_path / "refresh", now=lambda: NOW, **kwargs
    )


def saved(engine, identifier):
    with Session(engine) as session:
        asset, state = load_refresh(session, identifier)
        return state, Path(asset.storage_path).parent


def total(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_complete_refresh_and_cached_retry_do_not_refetch(refresh_engine, tmp_path):
    client = FakeRefreshClient(tmp_path / "raw")
    first = execute(refresh_engine, client, tmp_path)
    assert first.coverage == "complete" and len(client.calls) == 2
    assert all(p.counts.fetched == 3 and p.counts.accepted == 3 for p in first.products)
    state, folder = saved(refresh_engine, first.refresh_id)
    handoff = json.loads((folder / "handoff.json").read_text())
    assert handoff["normalized_records_count"] == 6
    assert (
        hashlib.sha256((folder / "normalized-records.jsonl").read_bytes()).hexdigest()
        == handoff["normalized_records_sha256"]
    )
    assert not handoff["pipeline_run_completed"] and total(refresh_engine, Run) == 0
    again = refresh_firms(
        refresh_engine, None, resume=first.refresh_id, now=lambda: NOW + timedelta(days=5)
    )
    assert again == first
    assert len(client.calls) == 2 and total(refresh_engine, Detection) == 6
    assert all(c.attempts == 1 for c in state.chunks)


def test_overlapping_refresh_counts_global_duplicates_and_keeps_revisions(refresh_engine, tmp_path):
    client = FakeRefreshClient(tmp_path / "raw")
    first = execute(refresh_engine, client, tmp_path)
    _, folder = saved(refresh_engine, first.refresh_id)
    prior = (folder / "normalized-records.jsonl").read_bytes()
    later = NOW + timedelta(minutes=15)
    client.clock = lambda: later
    client.frp = "42"
    result = refresh_firms(
        refresh_engine, client, spec(as_of=later), output=tmp_path / "refresh", now=lambda: later
    )
    assert result.coverage == "complete"
    assert all(p.counts.accepted == 0 and p.counts.duplicate == 3 for p in result.products)
    assert total(refresh_engine, Detection) == 6 and total(refresh_engine, DetectionSource) == 12
    _, new_folder = saved(refresh_engine, result.refresh_id)
    values = [
        json.loads(line)
        for line in (new_folder / "normalized-records.jsonl").read_text().splitlines()
    ]
    assert len(values) == 4 and all(row["observation"]["frp_mw"] == 42 for row in values)
    assert (folder / "normalized-records.jsonl").read_bytes() == prior


def test_failure_is_partial_and_resume_only_retries_failed_chunks(refresh_engine, tmp_path):
    client = FakeRefreshClient(tmp_path / "raw")
    body = spec()
    first_day = body.window_start.date()
    client.failures[(Product.NOAA21_NRT, first_day)] = [
        FirmsError("NETWORK", retryable=True, retry_after_seconds=60)
    ]
    result = execute(refresh_engine, client, tmp_path, body)
    assert result.coverage == "partial"
    assert result.products[1].counts is None and result.products[1].coverage == "unavailable"
    assert total(refresh_engine, Detection) == 3
    state, folder = saved(refresh_engine, result.refresh_id)
    assert state.chunks[1].error_code == "NETWORK"
    assert json.loads((folder / "handoff.json").read_text())["normalized_records_count"] == 3
    refresh_firms(refresh_engine, client, resume=result.refresh_id, now=lambda: NOW)
    assert len(client.calls) == 2
    later = NOW + timedelta(seconds=61)
    client.clock = lambda: later
    done = refresh_firms(refresh_engine, client, resume=result.refresh_id, now=lambda: later)
    assert done.coverage == "complete" and len(client.calls) == 3
    assert total(refresh_engine, Detection) == 6


def test_failed_old_daily_chunk_cannot_be_hidden_by_later_success(api, tmp_path):
    client_http, engine, _ = api
    add_region(engine)
    client_http.app.state.clock = lambda: NOW
    fake = FakeRefreshClient(tmp_path / "raw")
    body = spec(chunk_days=1)
    fake.failures[(Product.NOAA20_NRT, body.window_start.date())] = [
        FirmsError("DEADLINE", retryable=True, retry_after_seconds=600)
    ]
    result = execute(engine, fake, tmp_path, body)
    assert result.coverage == "partial"
    response = client_http.get("/api/v1/status")
    assert response.status_code == 200
    rows = {row["product"]: row for row in response.json()["sources"]}
    failed = rows[Product.NOAA20_NRT]
    assert failed["status"] == "degraded" and failed["coverage"] == "partial"
    assert (
        failed["fetched_count"] is None
        and failed["completed_chunks"] == 2
        and failed["total_chunks"] == 3
    )
    assert failed["latest_attempt_succeeded"] is False
    assert failed["next_retry_at"] is not None
    okay = rows[Product.NOAA21_NRT]
    assert okay["status"] == "ok" and okay["fetched_count"] == 3
    text_body = response.text
    assert str(tmp_path) not in text_body and "manifest_path" not in text_body


def test_successful_empty_and_all_failed_sources_remain_distinct(refresh_engine, tmp_path):
    fake = FakeRefreshClient(tmp_path / "raw")
    fake.empty = {Product.NOAA20_NRT, Product.NOAA21_NRT}
    result = execute(refresh_engine, fake, tmp_path)
    assert result.coverage == "complete" and all(p.counts.fetched == 0 for p in result.products)
    assert total(refresh_engine, Detection) == 0
    later = NOW + timedelta(minutes=1)
    fake.clock = lambda: later
    body = spec(as_of=later)
    for request in body.requests(saved(refresh_engine, result.refresh_id)[0].bbox):
        fake.failures[(request.product, request.start_date)] = [FirmsError("AUTHENTICATION")]
    failed = refresh_firms(
        refresh_engine, fake, body, output=tmp_path / "refresh", now=lambda: later
    )
    assert failed.coverage == "unavailable" and all(p.counts is None for p in failed.products)


def test_raw_response_recovered_after_crash_without_network(refresh_engine, tmp_path, monkeypatch):
    import firewatch.pipeline.firms_refresh as service

    fake = FakeRefreshClient(tmp_path / "raw")
    body = spec(products=[Product.NOAA20_NRT])
    original = service._publish_source
    monkeypatch.setattr(
        service,
        "_publish_source",
        lambda *a: (_ for _ in ()).throw(RuntimeError("crash after fetch")),
    )
    with pytest.raises(RuntimeError, match="crash"):
        execute(refresh_engine, fake, tmp_path, body)
    with Session(refresh_engine) as session:
        parent = session.scalar(
            select(SourceAsset).where(SourceAsset.product == "firms-refresh-v1")
        )
        identifier = parent.id
        assert session.scalar(select(IngestionBatch.status)) == JobStatus.RUNNING
    assert total(refresh_engine, Detection) == 0 and len(fake.calls) == 1
    monkeypatch.setattr(service, "_publish_source", original)
    result = refresh_firms(refresh_engine, None, resume=identifier, now=lambda: NOW)
    assert result.coverage == "complete" and total(refresh_engine, Detection) == 3
    assert len(fake.calls) == 1


def test_corrupt_or_missing_saved_raw_does_not_become_success(
    refresh_engine, tmp_path, monkeypatch
):
    import firewatch.pipeline.firms_refresh as service

    fake = FakeRefreshClient(tmp_path / "raw")
    body = spec(products=[Product.NOAA20_NRT])
    original = service._publish_source
    monkeypatch.setattr(
        service, "_publish_source", lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError):
        execute(refresh_engine, fake, tmp_path, body)
    with Session(refresh_engine) as session:
        parent = session.scalar(
            select(SourceAsset).where(SourceAsset.product == "firms-refresh-v1")
        )
        identifier = parent.id
    state, _ = saved(refresh_engine, identifier)
    manifest = Path(state.chunks[0].fetch_manifest_path)
    manifest.with_name("response-02.bin").write_bytes(b"changed")
    monkeypatch.setattr(service, "_publish_source", original)
    result = refresh_firms(refresh_engine, fake, resume=identifier, now=lambda: NOW)
    assert result.coverage == "complete" and len(fake.calls) == 2
    with Session(refresh_engine) as session:
        assert JobStatus.FAILED in list(session.scalars(select(IngestionBatch.status)))


def test_key_conflict_and_disabled_region_are_rejected_before_fetch(refresh_engine, tmp_path):
    fake = FakeRefreshClient(tmp_path / "raw")
    execute(refresh_engine, fake, tmp_path, spec(request_key="same"))
    later = NOW + timedelta(minutes=1)
    with pytest.raises(ValueError, match="conflicts"):
        refresh_firms(
            refresh_engine, fake, spec(request_key="same", as_of=later), now=lambda: later
        )
    assert len(fake.calls) == 2
    with Session(refresh_engine) as session, session.begin():
        session.scalar(select(Region)).enabled = False
    with pytest.raises(ValueError, match="enabled"):
        execute(refresh_engine, fake, tmp_path, spec(request_key="disabled"))
    allowed = execute(
        refresh_engine, fake, tmp_path, spec(request_key="disabled"), allow_disabled=True
    )
    assert allowed.coverage == "complete"


def test_row_quality_rejections_degrade_coverage(refresh_engine, tmp_path):
    fake = FakeRefreshClient(tmp_path / "raw")
    fake.invalid_first = True
    result = execute(refresh_engine, fake, tmp_path)
    assert result.coverage == "partial"
    assert all(p.counts.rejected == 1 and p.counts.fetched == 3 for p in result.products)
    _, folder = saved(refresh_engine, result.refresh_id)
    assert json.loads((folder / "handoff.json").read_text())["normalized_records_count"] == 4


def test_one_region_has_one_refresh_owner(refresh_engine, tmp_path, monkeypatch):
    fake = FakeRefreshClient(tmp_path / "raw")
    started = Event()
    release = Event()
    original = fake.fetch

    def paused(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(fake, "fetch", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(execute, refresh_engine, fake, tmp_path, spec(request_key="one"))
        assert started.wait(10)
        try:
            with pytest.raises(ValueError, match="owns"):
                execute(
                    refresh_engine,
                    FakeRefreshClient(tmp_path / "other"),
                    tmp_path,
                    spec(request_key="two"),
                )
        finally:
            release.set()
        assert task.result().coverage == "complete"


def test_single_connection_pool_and_freshness_guards(refresh_engine, tmp_path):
    with refresh_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    assert schema.startswith("fw_test_")
    engine = create_engine(
        refresh_engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )
    try:
        result = execute(engine, FakeRefreshClient(tmp_path / "raw"), tmp_path)
        assert result.coverage == "complete"
        for cutoff in (NOW + timedelta(seconds=1), NOW - timedelta(hours=73)):
            with pytest.raises(ValueError):
                execute(engine, None, tmp_path, spec(as_of=cutoff))
    finally:
        engine.dispose()


def test_status_keeps_latest_requested_product_even_after_other_sensor_only_refresh(api, tmp_path):
    http, engine, _ = api
    add_region(engine)
    fake = FakeRefreshClient(tmp_path / "raw")
    body = spec(chunk_days=1)
    fake.failures[(Product.NOAA21_NRT, body.window_start.date())] = [FirmsError("NETWORK")]
    first = execute(engine, fake, tmp_path, body)
    later = NOW + timedelta(minutes=15)
    fake.clock = lambda: later
    http.app.state.clock = lambda: later
    refresh_firms(
        engine,
        fake,
        spec(as_of=later, products=[Product.NOAA20_NRT]),
        output=tmp_path / "refresh",
        now=lambda: later,
    )
    result = http.get("/api/v1/status")
    assert result.status_code == 200
    sources = {row["product"]: row for row in result.json()["sources"]}
    assert sources[Product.NOAA21_NRT]["refresh_id"] == str(first.refresh_id)
    assert sources[Product.NOAA21_NRT]["coverage"] == "partial"
    assert sources[Product.NOAA21_NRT]["fetched_count"] is None


def test_retrying_one_sensor_does_not_make_old_other_sensor_retrieval_fresh(api, tmp_path):
    http, engine, _ = api
    add_region(engine)
    fake = FakeRefreshClient(tmp_path / "raw")
    fake.failures[(Product.NOAA21_NRT, spec().window_start.date())] = [
        FirmsError("NETWORK", retryable=True, retry_after_seconds=60)
    ]
    first = execute(engine, fake, tmp_path)
    later = NOW + timedelta(hours=25)
    fake.clock = lambda: later
    http.app.state.clock = lambda: later
    refresh_firms(engine, fake, resume=first.refresh_id, now=lambda: later)
    result = http.get("/api/v1/status")
    assert result.status_code == 200
    sources = {row["product"]: row for row in result.json()["sources"]}
    assert sources[Product.NOAA20_NRT]["status"] == "stale"
    # Phase 3.5 also marks observed acquisitions older than 24h stale,
    # even when that sensor was just successfully retrieved.
    assert sources[Product.NOAA21_NRT]["status"] == "stale"

    def retrieved(product):
        value = sources[product]["last_successful_retrieval_at"]
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    assert retrieved(Product.NOAA20_NRT) == NOW
    assert retrieved(Product.NOAA21_NRT) == later


def test_recovery_normalization_failure_is_failed_unknown_not_forever_running(
    refresh_engine, tmp_path, monkeypatch
):
    import firewatch.pipeline.firms_refresh as service

    fake = FakeRefreshClient(tmp_path / "raw")
    monkeypatch.setattr(
        service, "_publish_source", lambda *a: (_ for _ in ()).throw(RuntimeError("crash"))
    )
    with pytest.raises(RuntimeError):
        execute(refresh_engine, fake, tmp_path, spec(products=[Product.NOAA20_NRT]))
    with Session(refresh_engine) as session:
        identifier = session.scalar(
            select(SourceAsset.id).where(SourceAsset.product == "firms-refresh-v1")
        )
    monkeypatch.setattr(
        service,
        "_publish_source",
        lambda *a: (_ for _ in ()).throw(ValueError("PRIVATE_INPUT_VALUE")),
    )
    result = refresh_firms(refresh_engine, None, resume=identifier, now=lambda: NOW)
    assert result.coverage == "unavailable" and result.products[0].counts is None
    state, folder = saved(refresh_engine, identifier)
    assert state.chunks[0].status == "failed" and state.chunks[0].error_code == "NORMALIZATION"
    assert "PRIVATE_INPUT_VALUE" not in (folder / "handoff.json").read_text()
