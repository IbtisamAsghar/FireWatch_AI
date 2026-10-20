"""Deterministic synthetic acceptance records, restricted to disposable demo schemas."""

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text
from sqlalchemy.orm import Session

from firewatch.db.models import (
    Alert,
    ClusterMember,
    ClusterSnapshotRecord,
    FeatureSetRecord,
    IngestionBatch,
    Job,
    Region,
    Run,
    Score,
    SourceAsset,
)
from firewatch.repositories.detections import save_detection
from firewatch.repositories.runs import complete_and_publish_run
from firewatch.schemas.common import (
    Completeness,
    Confidence,
    JobStatus,
    JobType,
    Priority,
    Product,
    Provider,
    RunMode,
    RunStatus,
    ScoreKind,
    Sensor,
    Stage,
)
from firewatch.schemas.contracts import DetectionInput

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/sample/synthetic-demo-v1.json"
LABEL = "synthetic-demo-v1"


def identifier(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, "https://firewatch.invalid/synthetic-demo-v1/" + name)


IDS = {
    name: identifier(name)
    for name in (
        "region",
        "live",
        "replay",
        "empty",
        "pending",
        "live-critical",
        "live-low",
        "replay-high",
        "alert",
        "job",
    )
}


def seed_demo(session: Session) -> dict[str, UUID]:
    database, schema = session.execute(text("SELECT current_database(), current_schema()")).one()
    if not database.endswith("_test") or not re.fullmatch(r"fw_demo_[0-9a-f]{32}", schema):
        raise ValueError(
            "Synthetic demo writes require a disposable fw_demo schema in a _test database"
        )
    if session.get(Region, IDS["region"]) is not None:
        if not all(
            session.get(Run, IDS[name]) is not None
            for name in ("live", "replay", "empty", "pending")
        ):
            raise ValueError("Incomplete demo fixture; create a fresh disposable schema")
        return dict(IDS)
    fixture_bytes = FIXTURE.read_bytes()
    data = json.loads(fixture_bytes)
    if data["schema_version"] != LABEL or data["synthetic"] is not True:
        raise ValueError("Expected versioned explicitly synthetic fixture")
    cutoff = {
        mode: datetime.fromisoformat(data[f"{mode}_as_of"].replace("Z", "+00:00"))
        for mode in ("live", "replay")
    }
    area = Region(
        id=IDS["region"],
        name=LABEL,
        enabled=False,
        west=70,
        south=33,
        east=74,
        north=36,
        description=data["notice"],
    )
    session.add(area)
    session.flush()
    for mode in (RunMode.LIVE, RunMode.REPLAY):
        at = cutoff[mode.value]
        manifest = {
            "synthetic": True,
            "fixture": LABEL,
            "freshness": {
                "latest_weather_valid_at": (at - timedelta(hours=2)).isoformat(),
                "latest_retrieved_at": at.isoformat(),
            },
        }
        if mode == RunMode.REPLAY:
            manifest["replay_scenario"] = {
                "id": LABEL,
                "name": "Synthetic historical acceptance fixture",
                "description": data["notice"],
            }
        run = Run(
            id=IDS[mode.value],
            region_id=area.id,
            mode=mode,
            as_of=at,
            status=RunStatus.RUNNING,
            config_hash=hashlib.sha256(fixture_bytes).hexdigest(),
            run_key=LABEL + ":" + mode.value,
            input_asset_manifest=manifest,
            completeness=Completeness.COMPLETE,
            stage_status={stage.value: "succeeded" for stage in Stage},
            scorer_version="baseline-v1",
            feature_version="features-v1",
            created_at=at,
            started_at=at,
        )
        asset = SourceAsset(
            id=identifier(mode.value + "-asset"),
            provider=Provider.FIRMS,
            product=Product.NOAA20_NRT,
            request_metadata={"synthetic": True},
            retrieved_at=at,
            storage_path="data/sample/synthetic-demo-v1.json",
            content_hash=hashlib.sha256(fixture_bytes).hexdigest(),
            format="json",
            processing_version=LABEL,
        )
        session.add_all([run, asset])
        session.flush()
        records = [row for row in data["snapshots"] if row["mode"] == mode]
        batch = IngestionBatch(
            id=identifier(mode.value + "-batch"),
            region_id=area.id,
            product=Product.NOAA20_NRT,
            window_start=at - timedelta(hours=6),
            window_end=at,
            status=JobStatus.SUCCEEDED,
            asset_id=asset.id,
            fetched_count=len(records),
            accepted_count=len(records),
        )
        session.add(batch)
        session.flush()
        for row in records:
            name = row["name"]
            acquired = at - timedelta(hours=1)
            detection = save_detection(
                session,
                batch.id,
                area.id,
                DetectionInput(
                    product=Product.NOAA20_NRT,
                    sensor=Sensor.NOAA20,
                    latitude=row["latitude"],
                    longitude=row["longitude"],
                    acquired_at=acquired,
                    frp_mw=row["frp_mw"],
                    confidence=Confidence.UNKNOWN,
                    raw_values={"synthetic": True},
                    quality_flags=["synthetic_fixture"],
                    dedup_key=hashlib.sha256(name.encode()).hexdigest(),
                ),
            )
            snapshot = ClusterSnapshotRecord(
                id=IDS[name],
                run_id=run.id,
                latitude=row["latitude"],
                longitude=row["longitude"],
                member_count=1,
                frp_max_mw=row["frp_mw"],
                frp_sum_mw=row["frp_mw"],
                latest_detection_at=acquired,
                algorithm_version=LABEL,
            )
            session.add(snapshot)
            session.flush()
            values = {
                "frp_max_mw": row["frp_mw"],
                "temperature_c": row["temperature_c"],
                "wind_ms": row["wind_ms"],
            }
            session.add_all(
                [
                    ClusterMember(snapshot_id=snapshot.id, detection_id=detection.id),
                    FeatureSetRecord(
                        snapshot_id=snapshot.id,
                        run_id=run.id,
                        feature_version="features-v1",
                        values=values,
                        missing_flags={key: value is None for key, value in values.items()},
                        units={"frp_max_mw": "MW", "temperature_c": "degC", "wind_ms": "m/s"},
                        lineage={"synthetic": True, "quality_flags": ["synthetic_fixture"]},
                        feature_hash=hashlib.sha256(
                            json.dumps(values, sort_keys=True).encode()
                        ).hexdigest(),
                    ),
                ]
            )
            score = Score(
                id=identifier(name + "-score"),
                snapshot_id=snapshot.id,
                run_id=run.id,
                scorer_version="baseline-v1",
                priority_score=row["priority_score"],
                score_kind=ScoreKind.BASELINE,
                priority=Priority(row["priority"]),
                explanation={"reasons": [data["notice"]], "synthetic": True},
                scored_at=at + timedelta(minutes=1),
            )
            session.add(score)
            session.flush()
            if score.priority == Priority.CRITICAL:
                raw = json.dumps(
                    {
                        "synthetic": True,
                        "fixture": LABEL,
                        "run_id": str(run.id),
                        "snapshot_id": str(snapshot.id),
                        "frp_mw": row["frp_mw"],
                        "notice": data["notice"],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                session.add(
                    Alert(
                        id=IDS["alert"],
                        snapshot_id=snapshot.id,
                        run_id=run.id,
                        score_id=score.id,
                        canonical_payload=raw,
                        original_hash=hashlib.sha256(raw).hexdigest(),
                        evidence_schema_version="evidence-v1",
                        created_at=at + timedelta(minutes=1),
                    )
                )
        complete_and_publish_run(session, run.id, at + timedelta(minutes=2))
    at = cutoff["replay"]
    empty = Run(
        id=IDS["empty"],
        region_id=area.id,
        mode=RunMode.REPLAY,
        as_of=at,
        status=RunStatus.RUNNING,
        config_hash="e" * 64,
        run_key=LABEL + ":empty",
        input_asset_manifest={"synthetic": True},
        completeness=Completeness.COMPLETE,
        stage_status={stage.value: "succeeded" for stage in Stage},
        scorer_version="baseline-v1",
        feature_version="features-v1",
        created_at=at,
        started_at=at,
    )
    session.add(empty)
    session.flush()
    complete_and_publish_run(session, empty.id, at + timedelta(minutes=2))
    session.add_all(
        [
            Run(
                id=IDS["pending"],
                region_id=area.id,
                mode=RunMode.LIVE,
                as_of=cutoff["live"],
                status=RunStatus.QUEUED,
                config_hash="f" * 64,
                run_key=LABEL + ":pending",
                input_asset_manifest={"synthetic": True},
                completeness=Completeness.UNAVAILABLE,
                stage_status={stage.value: "pending" for stage in Stage},
                scorer_version="baseline-v1",
                feature_version="features-v1",
                created_at=cutoff["live"],
            ),
            Job(
                id=IDS["job"],
                type=JobType.REFRESH,
                status=JobStatus.QUEUED,
                payload={"synthetic": True, "fixture": LABEL},
                idempotency_key=LABEL,
                payload_hash="a" * 64,
                available_at=cutoff["live"],
            ),
        ]
    )
    session.flush()
    return dict(IDS)


def read_requests() -> dict[str, str]:
    return {
        "health_live": "/health/live",
        "health_ready": "/health/ready",
        "list_regions": "/api/v1/regions",
        "get_status": "/api/v1/status",
        "list_runs": f"/api/v1/runs?region_id={IDS['region']}",
        "get_run": f"/api/v1/runs/{IDS['live']}",
        "list_hotspots": f"/api/v1/hotspots?run_id={IDS['live']}",
        "get_hotspot": f"/api/v1/hotspots/{IDS['live-critical']}",
        "get_hotspots_geojson": f"/api/v1/hotspots.geojson?run_id={IDS['live']}",
        "get_summary": f"/api/v1/summary?run_id={IDS['live']}",
        "get_current_model": "/api/v1/models/current",
        "list_replays": "/api/v1/replays",
        "get_alert": f"/api/v1/alerts/{IDS['alert']}",
        "verify_alert": f"/api/v1/alerts/{IDS['alert']}/verify",
        "get_job": f"/api/v1/jobs/{IDS['job']}",
    }
