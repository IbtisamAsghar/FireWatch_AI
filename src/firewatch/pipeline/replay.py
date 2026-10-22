"""Trusted scenario admission and synchronous offline replay using the durable worker."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.models import Alert, FeatureSetRecord, Run, Score
from firewatch.pipeline.jobs import LeaseLost, claim, finish
from firewatch.pipeline.replay_bundle import import_bundle, load_bundle
from firewatch.pipeline.runner import execute_run
from firewatch.repositories.jobs import enqueue, replay_payload
from firewatch.schemas.common import JobType
from firewatch.schemas.operator import ReplayConfig, ReplayRequest, ReplayScenario
from firewatch.worker import keep_alive


def scenario_config(path: Path, identifier: str) -> ReplayScenario:
    with path.open("rb") as source:
        raw = source.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        raise ValueError("Replay catalog exceeds size bound")
    config = ReplayConfig.model_validate(yaml.safe_load(raw))
    scenario = next((s for s in config.scenarios if s.id == identifier), None)
    if scenario is None:
        raise ValueError("Unknown allowlisted replay scenario")
    return scenario


def replay_scenario(
    engine: Engine,
    settings: Settings,
    identifier: str,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Import verified portable inputs, admit durable work, return the completed run ID."""
    scenario = scenario_config(settings.replays_config, identifier)
    if not scenario.bundle_sha256:
        raise ValueError("Prepare a checksummed bundle before using the replay CLI")
    root = settings.replay_bundle_dir / scenario.id
    bundle = load_bundle(root, scenario.bundle_sha256)
    if bundle.scenario != scenario.model_copy(update={"bundle_sha256": None}):
        raise ValueError("Replay catalog differs from the bundle's scenario")
    with Session(engine) as session, session.begin():
        import_bundle(session, root, bundle)
    request = ReplayRequest(scenario_id=scenario.id)
    with Session(engine) as session:
        job, _ = enqueue(
            session,
            settings,
            JobType.REPLAY,
            "replay-" + uuid4().hex,
            request.model_dump(mode="json"),
            now(),
            lambda: replay_payload(session, settings, request, now()),
        )
    with Session(engine) as session, session.begin():
        lease = claim(session, settings, now(), job_id=job.id)
    if lease is None:
        return {
            "job_id": str(job.id),
            "status": "queued",
            "mode": "replay",
            "message": "Another worker owns this cutoff; inspect the durable job status.",
        }
    try:
        with keep_alive(engine, settings, lease, now) as lost:
            run_id = execute_run(engine, settings, lease, now)
            if lost.is_set():
                raise LeaseLost("Replay heartbeat failed")
            with Session(engine) as session, session.begin():
                finish(session, lease, settings, now())
    except LeaseLost:
        raise
    except Exception:
        with Session(engine) as session, session.begin():
            finish(session, lease, settings, now(), error="REPLAY_FAILED", retryable=False)
        raise ValueError(
            "Replay execution failed; inspect job status and verify the bundle"
        ) from None
    with Session(engine) as session:
        run = session.get(Run, run_id)
        assert run is not None
        hashes = list(
            session.scalars(
                select(FeatureSetRecord.feature_hash)
                .where(FeatureSetRecord.run_id == run_id)
                .order_by(FeatureSetRecord.snapshot_id)
            )
        )
        scores = list(
            session.scalars(select(Score).where(Score.run_id == run_id).order_by(Score.snapshot_id))
        )
        return {
            "job_id": str(job.id),
            "run_id": str(run_id),
            "scenario_id": scenario.id,
            "mode": run.mode.value,
            "status": run.status.value,
            "as_of": run.as_of.isoformat(),
            "processed_at": run.completed_at.isoformat() if run.completed_at else None,
            "feature_hashes": hashes,
            "priority_scores": [s.priority_score for s in scores],
            "priorities": [s.priority.value for s in scores],
            "scored_at": [s.scored_at.isoformat() for s in scores],
            "completeness": run.completeness.value,
            "coverage_warnings": run.input_asset_manifest.get("coverage_warnings", []),
            "alerts": len(list(session.scalars(select(Alert.id).where(Alert.run_id == run_id)))),
            "data_kind": bundle.data_kind,
            "scenario_kind": bundle.scenario_kind,
            "bundle_sha256": scenario.bundle_sha256,
        }
