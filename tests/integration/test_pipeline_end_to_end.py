"""One offline journey through import, enrichment, worker publication and HTTP evidence."""

import hashlib
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from conftest import migrate
from fastapi.testclient import TestClient
from firms_factory import NOW
from inference_factory import candidate
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_feature_persistence import warm
from test_model_registry import prepare_ml_work
from test_snapshot_persistence import setup_sources
from test_worker import queued, settings
from weather_factory import NOW as ENRICHMENT_RETRIEVED

from firewatch.db.models import (
    Alert,
    FeatureSetRecord,
    IngestionBatch,
    Job,
    LiveRunHead,
    Run,
    Score,
)
from firewatch.db.session import get_session
from firewatch.main import create_app
from firewatch.repositories.ingestion import persist_normalized
from firewatch.repositories.snapshots import build_run_snapshots
from firewatch.schemas.api import HotspotDetail, HotspotPage, Summary, Verification
from firewatch.sources.firms import FirmsAreaRequest, FirmsClient
from firewatch.sources.firms_normalize import normalize_firms_batch
from firewatch.worker import process_one

pytestmark = pytest.mark.integration
CLOCK = ENRICHMENT_RETRIEVED + timedelta(days=1)


@pytest.fixture(scope="module")
def synthetic_model():
    # Deterministic local fitting and JSON export; no external artifact or pickle input.
    return candidate()


@pytest.mark.parametrize(
    "enriched,ml",
    [(True, False), (False, False), (False, True)],
    ids=["cached-enrichment", "missing-enrichment", "synthetic-approved-model"],
)
def test_import_to_worker_to_frontend_and_verification(
    empty_engine, tmp_path, monkeypatch, enriched, ml, request
):
    def no_provider(*args, **kwargs):
        raise AssertionError("Offline acceptance attempted external HTTP")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_provider)
    migrate(empty_engine)
    config = settings(tmp_path)
    fixtures = Path(__file__).parents[1] / "fixtures/providers"

    def provider(request):
        assert request.url.host == "firms.modaps.eosdis.nasa.gov"
        name = "firms-quota.json" if "mapkey_status" in request.url.path else "firms-area.txt"
        return httpx.Response(200, content=(fixtures / name).read_bytes())

    with Session(empty_engine) as session, session.begin():
        area, preparation, policy = setup_sources(session, tmp_path)
        area.enabled = True
        with FirmsClient(
            SecretStr("synthetic-firms-test-key-1234567890"),
            tmp_path / "firms-transcript",
            transport=httpx.MockTransport(provider),
            now=lambda: NOW + timedelta(hours=1),
        ) as adapter:
            raw = adapter.fetch(
                FirmsAreaRequest.model_validate(
                    {
                        "product": "VIIRS_NOAA20_NRT",
                        "start_date": "2025-06-10",
                        "days": 1,
                        "bbox": {"west": 70, "south": 33, "east": 74, "north": 36},
                    }
                )
            )
        persist_normalized(session, normalize_firms_batch(raw), area.id)
        snapshots = build_run_snapshots(session, preparation.id, policy)
        if enriched:
            warm(session, tmp_path, snapshots)
        assets = list(session.scalars(select(IngestionBatch.asset_id)))
        job_id = queued(session, area.id, assets=assets)
        area_id = area.id
    if ml:
        prepare_ml_work(
            (empty_engine, config, job_id, area_id), request.getfixturevalue("synthetic_model")
        )
    assert process_one(empty_engine, config, now=lambda: CLOCK)
    # No remaining work; duplicate polling cannot publish a second run/alert.
    assert not process_one(empty_engine, config, now=lambda: CLOCK)
    with Session(empty_engine) as session:
        job = session.get(Job, job_id)
        assert job.status == "succeeded"
        run_id = job.result_run_id
        run = session.get(Run, run_id)
        assert run.status == "completed" and run.mode == "replay"
        assert all(value == "succeeded" for value in run.stage_status.values())
        assert session.get(LiveRunHead, area_id) is None
        feature = session.scalar(select(FeatureSetRecord).where(FeatureSetRecord.run_id == run_id))
        assert (
            (feature.values["temperature_c"] == 10)
            if enriched
            else (feature.values["temperature_c"] is None)
        )
        assert session.scalar(select(func.count()).select_from(Score)) == 1
        score = session.scalar(select(Score).where(Score.run_id == run_id))
        assert (score.model_id is not None) == ml
        alert = session.scalar(select(Alert).where(Alert.run_id == run_id))
        alert_id, original, digest = alert.id, alert.canonical_payload, alert.original_hash
        assert hashlib.sha256(original).hexdigest() == digest
    app = create_app(config)

    def sessions():
        with Session(empty_engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    with TestClient(app) as client:
        app.state.clock = lambda: CLOCK
        params = {"run_id": str(run_id)}
        listing = client.get("/api/v1/hotspots", params=params)
        assert listing.status_code == 200, listing.text
        page = HotspotPage.model_validate(listing.json())
        assert page.total == 1
        if ml:
            assert page.items[0].score_kind == "probability"
            assert page.items[0].escalation_probability is not None
        else:
            assert page.items[0].priority_score == 150
            assert page.items[0].escalation_probability is None
        snapshot_id = page.items[0].snapshot_id
        detail = client.get(f"/api/v1/hotspots/{snapshot_id}", params=params)
        assert detail.status_code == 200, detail.text
        HotspotDetail.model_validate(detail.json())
        summary = Summary.model_validate(client.get("/api/v1/summary", params=params).json())
        assert summary.total == 1
        geo = client.get("/api/v1/hotspots.geojson", params=params).json()
        assert len(geo["features"]) == 1
        assert geo["features"][0]["geometry"]["coordinates"][0] == pytest.approx(71.12345)
        verified = client.get(
            f"/api/v1/alerts/{alert_id}/verify", params={"verify_artifacts": "true"}
        )
        assert verified.status_code == 200, verified.text
        result = Verification.model_validate(verified.json())
        assert result.local_status == "local_match" and result.computed_hash == digest
        assert result.artifact_verification == "verified"
        assert result.anchor_status == "unanchored"
        assert config.admin_api_token.get_secret_value() not in verified.text
        assert client.get("/api/v1/regions").status_code == 200
        progress = client.get(
            f"/api/v1/jobs/{job_id}",
            headers={"Authorization": "Bearer " + config.admin_api_token.get_secret_value()},
        )
        assert UUID(progress.json()["result_run_id"]) == run_id
    with Session(empty_engine) as session:
        saved = session.get(Alert, alert_id)
        assert (saved.canonical_payload, saved.original_hash) == (original, digest)
        assert session.scalar(select(func.count()).select_from(Alert)) == 1
