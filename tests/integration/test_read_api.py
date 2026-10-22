"""Read API acceptance against real migrated PostgreSQL and immutable outputs."""

import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session
from test_schema import T, alert, batch, observation, outputs, region, running

from firewatch.db.models import (
    Alert,
    Anchor,
    AnchorOutbox,
    ClusterMember,
    ClusterSnapshotRecord,
    FeatureSetRecord,
    IngestionBatch,
    Job,
    ModelVersion,
    Region,
    Score,
    SourceAsset,
)
from firewatch.repositories.detections import save_detection
from firewatch.repositories.runs import complete_and_publish_run
from firewatch.schemas.common import (
    AnchorStatus,
    JobStatus,
    JobType,
    ModelStatus,
    Priority,
    Product,
    RunMode,
    ScoreKind,
)

pytestmark = pytest.mark.integration
TOKEN = "test-only-operator-" + "x" * 40


def seed(engine):
    with Session(engine, expire_on_commit=False) as session:
        area = region(session)
        live = running(session, area)
        one, score1 = outputs(session, live)
        two, score2 = outputs(session, live, Priority.LOW)
        score1.priority_score = 0.9
        score2.priority_score = 0.2
        two.longitude = 73
        evidence = alert(session, score1)
        replay = running(session, area, mode=RunMode.REPLAY)
        replay.input_asset_manifest = {
            "replay_scenario": {"id": "synthetic-history", "name": "Synthetic test fixture"},
            "freshness": {"latest_weather_valid_at": (T - timedelta(hours=2)).isoformat()},
            "database_url": "PRIVATE_MANIFEST_SECRET",
        }
        historic, _ = outputs(session, replay)
        empty = running(session, area, cutoff=T + timedelta(hours=1))
        pending = running(session, area, cutoff=T + timedelta(hours=2))
        for run in (live, replay, empty):
            complete_and_publish_run(session, run.id, T + timedelta(hours=4))
        job = Job(
            type=JobType.REFRESH,
            payload={"secret": "PRIVATE_JOB_SECRET"},
            idempotency_key=uuid4().hex,
            payload_hash="a" * 64,
            status=JobStatus.FAILED,
            error="postgresql://PRIVATE_JOB_SECRET",
        )
        session.add(job)
        session.commit()
        return dict(
            area=area.id,
            live=live.id,
            replay=replay.id,
            empty=empty.id,
            pending=pending.id,
            one=one.id,
            two=two.id,
            historic=historic.id,
            alert=evidence.id,
            job=job.id,
        )


def get(client, path, **query):
    response = client.get("/api/v1/" + path, params=query)
    assert response.status_code == 200, response.text
    return response.json()


def test_filters_ranking_summary_geojson_and_detail_share_run(api):
    client, engine, _ = api
    ids = seed(engine)
    page = get(client, "hotspots", run_id=ids["live"])
    assert page["limit"] == 50 and page["offset"] == 0 and page["total"] == 2
    assert [item["snapshot_id"] for item in page["items"]] == [str(ids["one"]), str(ids["two"])]
    assert page["mode"] == "live"
    assert page["items"][0]["escalation_probability"] is None
    assert page["items"][0]["alert_id"] == str(ids["alert"])
    query = dict(run_id=ids["live"], priority="HIGH", bbox="70,33,72,36")
    filtered = get(client, "hotspots", **query)
    summary = get(client, "summary", **query)
    geo = get(client, "hotspots.geojson", **query)
    assert filtered["total"] == summary["total"] == geo["total"] == 1
    assert summary["priority_counts"]["HIGH"] == 1
    assert geo["features"][0]["geometry"]["coordinates"] == [71, 34]
    assert geo["truncated"] is False
    detail = get(client, f"hotspots/{ids['one']}", run_id=ids["live"])
    assert detail["features"]["frp_max_mw"] is None
    assert detail["missing_flags"]["frp_max_mw"] is True
    assert detail["hotspot"] == page["items"][0]
    assert detail["evidence"]["verification"]["local_status"] == "local_match"
    historical = get(client, "hotspots", run_id=ids["replay"])
    assert historical["total"] == 1
    assert historical["items"][0]["snapshot_id"] == str(ids["historic"])
    assert historical["freshness"]["reference_kind"] == "historical_cutoff"
    assert historical["freshness"]["observation_age_hours"] == 1
    assert historical["freshness"]["weather_age_hours"] == 2
    assert (
        client.get(f"/api/v1/hotspots/{ids['one']}", params={"run_id": ids["replay"]}).status_code
        == 404
    )


@pytest.mark.parametrize("path", ["hotspots", "summary", "hotspots.geojson"])
def test_empty_completed_unknown_and_pending_runs(api, path):
    client, engine, _ = api
    ids = seed(engine)
    result = get(client, path, run_id=ids["empty"])
    assert result["total"] == 0
    assert result.get("items", result.get("features", [])) == []
    assert result["freshness"]["latest_detection_at"] is None
    assert client.get("/api/v1/" + path, params={"run_id": uuid4()}).status_code == 404
    pending = client.get("/api/v1/" + path, params={"run_id": ids["pending"]})
    assert pending.status_code == 503 and pending.json()["error"]["retryable"]


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"run_id": "not-a-uuid"},
        {"run_id": str(uuid4()), "limit": 201},
        {"run_id": str(uuid4()), "offset": -1},
        {"run_id": str(uuid4()), "priority": "urgent"},
        {"run_id": str(uuid4()), "bbox": "1,2,3"},
        {"run_id": str(uuid4()), "bbox": "nan,0,1,1"},
        {"run_id": str(uuid4()), "bbox": "73,33,70,36"},
        {"run_id": str(uuid4()), "unexpected": "PRIVATE_QUERY_SECRET"},
    ],
)
def test_invalid_filters_are_safe_422(api, query):
    client, _, _ = api
    response = client.get("/api/v1/hotspots", params=query)
    assert response.status_code == 422
    assert "PRIVATE_QUERY_SECRET" not in response.text
    assert UUID(response.json()["error"]["request_id"])
    assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]


def test_history_regions_replays_and_protected_jobs(api):
    client, engine, settings = api
    ids = seed(engine)
    area = get(client, "regions")["items"][0]
    assert area["latest_live_run_id"] == str(ids["empty"])
    assert area["available_modes"] == ["live", "replay"]
    history = get(client, "runs", region_id=ids["area"], mode="replay")
    assert history["total"] == 1 and history["items"][0]["run_id"] == str(ids["replay"])
    assert "PRIVATE_MANIFEST_SECRET" not in json.dumps(history)
    replay = get(client, "replays")["items"][0]
    assert replay["scenario_id"] == "synthetic-history"
    assert replay["run_id"] == str(ids["replay"])
    assert get(client, f"runs/{ids['pending']}")["status"] == "running"
    assert client.get("/api/v1/runs", params={"region_id": uuid4()}).status_code == 404
    for header in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic wrong"}):
        response = client.get(f"/api/v1/jobs/{ids['job']}", headers=header)
        assert response.status_code == 401 and response.headers["WWW-Authenticate"] == "Bearer"
    auth = {"Authorization": "Bearer " + TOKEN}
    job = client.get(f"/api/v1/jobs/{ids['job']}", headers=auth)
    assert job.status_code == 200 and job.json()["failure"]
    assert "PRIVATE_JOB_SECRET" not in job.text and "payload" not in job.json()
    assert client.get(f"/api/v1/jobs/{uuid4()}", headers=auth).status_code == 404
    settings.public_reads_enabled = False
    assert client.get("/api/v1/regions").status_code == 401
    assert client.get("/api/v1/regions", headers=auth).status_code == 200
    assert client.get("/health/live").status_code == 200


def test_status_empty_success_and_failed_retry(api):
    client, engine, _ = api
    with Session(engine) as session:
        area = region(session)
        source = batch(session, area)
        source.fetched_count = source.accepted_count = 0
        source_id = source.id
        session.commit()
    result = get(client, "status")
    noaa20 = next(row for row in result["sources"] if row["product"] == Product.NOAA20_NRT)
    assert noaa20["status"] == "ok" and noaa20["latest_detection_at"] is None
    assert (
        next(row for row in result["sources"] if row["product"] == Product.NOAA21_NRT)["status"]
        == "never_fetched"
    )
    assert result["scorer"]["scoring_available"] is True
    with Session(engine) as session:
        original = session.get(IngestionBatch, source_id)
        failed = batch(session, session.get(Region, original.region_id))
        failed.status = JobStatus.FAILED
        failed.failure_reason = "PRIVATE_SOURCE_SECRET"
        session.get(SourceAsset, failed.asset_id).retrieved_at = T + timedelta(hours=1)
        session.commit()
    retry = get(client, "status")
    state = next(row for row in retry["sources"] if row["product"] == Product.NOAA20_NRT)
    assert state["status"] == "degraded"
    assert state["last_successful_retrieval_at"] == noaa20["last_successful_retrieval_at"]
    assert "PRIVATE_SOURCE_SECRET" not in json.dumps(retry)


def test_alert_hash_verification_never_writes_and_never_trusts_stored_anchor(api):
    client, engine, settings = api
    ids = seed(engine)
    url = f"alerts/{ids['alert']}"
    saved = get(client, url)
    assert saved["payload"] == {"fixture": "synthetic"}
    assert saved["verification"]["local_status"] == "local_match"
    assert saved["verification"]["anchor_status"] == "unanchored"
    with Session(engine) as session:
        session.add(AnchorOutbox(alert_id=ids["alert"], network="local"))
        session.commit()
    assert get(client, url + "/verify")["anchor_status"] == "anchor_pending"
    with Session(engine) as session:
        session.add(
            Anchor(
                alert_id=ids["alert"],
                chain_id=31337,
                contract_address="0x" + "a" * 40,
                transaction_hash="0x" + "b" * 64,
                anchored_digest=saved["original_hash"],
                confirmation_status=AnchorStatus.CONFIRMED,
                network_kind="local",
            )
        )
        session.commit()
        before = session.scalar(select(func.count()).select_from(AnchorOutbox))
    assert get(client, url + "/verify")["anchor_status"] == "anchor_unavailable"
    settings.chain_enabled = True

    class Reader:
        def read_digest(self, anchor):
            assert anchor.chain_id == 31337
            return saved["original_hash"]

    client.app.state.anchor_reader = Reader()
    assert get(client, url + "/verify")["anchor_status"] == "anchor_match"
    Reader.read_digest = lambda *_: "c" * 64
    assert get(client, url + "/verify")["anchor_status"] == "anchor_mismatch"
    Reader.read_digest = lambda *_: "invalid RPC response"
    assert get(client, url + "/verify")["anchor_status"] == "anchor_unavailable"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(AnchorOutbox)) == before
    assert client.get("/api/v1/" + url, params={"run_id": ids["replay"]}).status_code == 404


def test_ready_schema_and_model_artifact_checks(api):
    client, engine, settings = api
    assert client.get("/health/ready").status_code == 200
    settings.active_scorer = "approved-test-v1"
    assert client.get("/health/ready").status_code == 503
    artifact = settings.model_dir / "test.bin"
    artifact.write_bytes(b"synthetic model bytes, never loaded")
    with Session(engine) as session:
        model = ModelVersion(
            version=settings.active_scorer,
            artifact_path=artifact.name,
            artifact_hash=hashlib.sha256(artifact.read_bytes()).hexdigest(),
            feature_version="features-v1",
            training_manifest={},
            metrics={},
            target_definition={},
            status=ModelStatus.APPROVED,
        )
        session.add(model)
        session.commit()
    assert client.get("/health/ready").status_code == 200
    metadata = get(client, "models/current")
    assert metadata["artifact_ready"] and not metadata["scoring_available"]
    assert str(artifact) not in json.dumps(metadata)
    artifact.write_bytes(b"corrupt")
    assert client.get("/health/ready").status_code == 503
    settings.active_scorer = "baseline-v1"
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='0001'"))
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health/live").status_code == 200


def test_large_geojson_cap_and_uuid_ties(api):
    client, engine, _ = api
    with Session(engine) as session:
        area = region(session)
        run = running(session, area)
        source = batch(session, area)
        detection = save_detection(session, source.id, area.id, observation())
        identifiers = [uuid4() for _ in range(2001)]
        session.execute(
            insert(ClusterSnapshotRecord),
            [
                dict(
                    id=identifier,
                    run_id=run.id,
                    latitude=34,
                    longitude=71,
                    member_count=1,
                    latest_detection_at=detection.acquired_at,
                    algorithm_version="cluster-v1",
                )
                for identifier in identifiers
            ],
        )
        session.execute(
            insert(ClusterMember),
            [dict(snapshot_id=identifier, detection_id=detection.id) for identifier in identifiers],
        )
        session.execute(
            insert(FeatureSetRecord),
            [
                dict(
                    snapshot_id=identifier,
                    run_id=run.id,
                    feature_version="features-v1",
                    values={},
                    missing_flags={},
                    units={},
                    lineage={},
                    feature_hash="a" * 64,
                )
                for identifier in identifiers
            ],
        )
        session.execute(
            insert(Score),
            [
                dict(
                    snapshot_id=identifier,
                    run_id=run.id,
                    scorer_version="baseline-v1",
                    priority_score=0.7,
                    score_kind=ScoreKind.BASELINE,
                    priority=Priority.HIGH,
                    explanation={},
                    scored_at=T + timedelta(hours=4),
                )
                for identifier in identifiers
            ],
        )
        complete_and_publish_run(session, run.id, T + timedelta(hours=4))
        run_id = run.id
        session.commit()
    page = get(client, "hotspots", run_id=run_id, limit=200, offset=200)
    assert [UUID(item["snapshot_id"]) for item in page["items"]] == sorted(identifiers)[200:400]
    geo = get(client, "hotspots.geojson", run_id=run_id)
    assert geo["total"] == 2001 and geo["returned"] == geo["limit"] == 2000
    assert geo["truncated"] is True
    tail = get(client, "hotspots.geojson", run_id=run_id, offset=2000)
    assert tail["returned"] == 1 and tail["truncated"] is True
    assert (
        client.get("/api/v1/hotspots.geojson", params={"run_id": run_id, "limit": 2001}).status_code
        == 422
    )


def test_get_sessions_are_enforced_read_only(api, monkeypatch):
    client, engine, _ = api

    # This injected repository fault must be rejected even if future route code attempts writes.
    def faulty_repository(session):
        session.execute(
            text(
                "INSERT INTO regions(name,west,south,east,north) "
                "VALUES ('forbidden-write',70,33,74,36)"
            )
        )
        session.commit()
        return []

    monkeypatch.setattr("firewatch.api.routes.reads.region_rows", faulty_repository)
    assert client.get("/api/v1/regions").status_code == 503
    with Session(engine) as session:
        assert (
            session.scalar(text("SELECT count(*) FROM regions WHERE name='forbidden-write'")) == 0
        )


def test_evidence_projection_and_run_freshness_preserve_private_originals(api):
    client, engine, _ = api
    raw = b'{"temperature_c":30,"token":"PRIVATE_EVIDENCE_SECRET","request_url":"https://private.example/key"}'
    with Session(engine) as session:
        run = running(session, mode=RunMode.REPLAY)
        run.input_asset_manifest = {
            "freshness": {"latest_weather_valid_at": (T + timedelta(hours=1)).isoformat()},
            "storage_path": "PRIVATE_PATH_SECRET",
        }
        snapshot, score = outputs(session, run)
        score.explanation = {"reasons": ["Missing FRP"], "token": "PRIVATE_EXPLANATION_SECRET"}
        record = Alert(
            snapshot_id=snapshot.id,
            run_id=run.id,
            score_id=score.id,
            canonical_payload=raw,
            original_hash=hashlib.sha256(raw).hexdigest(),
            evidence_schema_version="evidence-v1",
        )
        session.add(record)
        complete_and_publish_run(session, run.id, T + timedelta(hours=4))
        snapshot_id, alert_id = snapshot.id, record.id
        session.commit()
    detail = get(client, f"hotspots/{snapshot_id}")
    assert detail["evidence"]["payload"] == {"temperature_c": 30}
    assert detail["evidence"]["payload_redacted"] is True
    assert detail["evidence"]["verification"]["local_status"] == "local_match"
    assert detail["freshness"]["latest_weather_valid_at"] is None
    assert "future_weather_metadata_ignored" in detail["freshness"]["quality_flags"]
    assert "PRIVATE_" not in json.dumps(detail)
    with Session(engine) as session:
        assert session.get(Alert, alert_id).canonical_payload == raw


def test_readiness_rejects_incompatible_approved_model(api):
    client, engine, settings = api
    artifact = settings.model_dir / "incompatible.bin"
    artifact.write_bytes(b"synthetic fixture")
    settings.active_scorer = "incompatible-test-v1"
    with Session(engine) as session:
        session.add(
            ModelVersion(
                version=settings.active_scorer,
                artifact_path=artifact.name,
                artifact_hash=hashlib.sha256(artifact.read_bytes()).hexdigest(),
                feature_version="features-other",
                training_manifest={},
                metrics={},
                target_definition={},
                status=ModelStatus.APPROVED,
            )
        )
        session.commit()
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ARTIFACT_UNAVAILABLE"
