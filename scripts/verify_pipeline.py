"""Phase 3.7: real process termination, isolated offline replay and Parquet handoff."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from firewatch.config import get_settings
from firewatch.db.models import Alert, Job, LiveRunHead, Region, Run
from firewatch.pipeline.handoff import export_handoff, verify_handoff
from firewatch.pipeline.jobs import Lease, claim
from firewatch.pipeline.network_guard import install_database_only_network_guard
from firewatch.pipeline.replay import replay_scenario
from firewatch.pipeline.replay_bundle import import_bundle, load_bundle
from firewatch.pipeline.runner import execute_run
from firewatch.repositories.jobs import enqueue, replay_payload
from firewatch.schemas.common import JobType, Stage
from firewatch.schemas.operator import ReplayConfig, ReplayRequest
from firewatch.worker import process_one


def child(kind: str) -> int:
    settings = get_settings()
    url = os.environ["FIREWATCH_ACCEPTANCE_DB"]
    schema = os.environ["FIREWATCH_ACCEPTANCE_SCHEMA"]
    if not schema.startswith("fw_accept_") or len(schema) != 42 or not schema[10:].isalnum():
        raise ValueError("Invalid acceptance schema")
    attempts = install_database_only_network_guard(url)
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )
    identifier = UUID(os.environ["FIREWATCH_ACCEPTANCE_JOB"])
    if kind == "crash":

        def terminate(stage):
            if stage == Stage.FEATURES:
                os._exit(23)  # Actual abrupt process termination, after a committed checkpoint.

        execute_run(
            engine, settings, Lease(identifier, 1), lambda: datetime.now(UTC), after_stage=terminate
        )
        raise RuntimeError("Crash checkpoint was not reached")
    assert process_one(engine, settings)
    with Session(engine) as session:
        job = session.get(Job, identifier)
        assert job.status == "succeeded" and job.attempts == 2
        recovered = job.result_run_id
    config = ReplayConfig.model_validate(
        yaml.safe_load(settings.replays_config.read_text(encoding="utf-8"))
    )
    results = [replay_scenario(engine, settings, s.id) for s in config.scenarios]
    with tempfile.TemporaryDirectory(prefix="firewatch-handoff-acceptance-") as folder:
        output = Path(folder) / "handoff"
        report = export_handoff(
            engine, settings, [UUID(r["run_id"]) for r in results], output, data_kind="real"
        )
        assert verify_handoff(output, report["manifest_sha256"])["verified"]
        import pyarrow.parquet as pq

        rows = pq.read_table(output / "features.parquet").to_pylist()
        degraded = {r["run_id"] for r in results if r["scenario_kind"] == "degraded"}
        assert any(r["run_id"] in degraded for r in rows)
        assert all(
            r["precipitation_24h_mm"] is None and r["precipitation_24h_mm_missing"]
            for r in rows
            if r["run_id"] in degraded
        )
    with Session(engine) as session:
        assert session.get(Run, recovered).status == "completed"
        assert session.scalar(select(LiveRunHead.region_id).limit(1)) is None
        assert not any(session.scalars(select(Region.enabled)))
        alerts = list(session.scalars(select(Alert)))
        assert len({a.score_id for a in alerts}) == len(alerts)
    assert attempts == []
    print(
        json.dumps(
            dict(
                schema_version="phase-3.7-child-v1",
                recovered_attempts=2,
                network_attempts=0,
                network_policy="Python DNS/socket DB-only; provider HTTP transports denied",
                handoff=report,
                scenarios=[
                    {
                        k: r[k]
                        for k in (
                            "scenario_id",
                            "run_id",
                            "mode",
                            "status",
                            "as_of",
                            "processed_at",
                            "completeness",
                            "alerts",
                        )
                    }
                    for r in results
                ],
                duplicate_alerts=False,
                live_pointer_unchanged=True,
                regions_remain_disabled=True,
            )
        )
    )
    engine.dispose()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=["crash", "recover"])
    args = parser.parse_args()
    if args.child:
        return child(args.child)
    settings = get_settings()
    catalog = ReplayConfig.model_validate(
        yaml.safe_load(settings.replays_config.read_text(encoding="utf-8"))
    )
    active = next(s for s in catalog.scenarios if "active" in s.id)
    url = make_url(settings.database_url.get_secret_value()).set(database="firewatch_test")
    schema = "fw_accept_" + uuid4().hex
    control = create_engine(url, hide_parameters=True)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )
    try:
        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        root = settings.replay_bundle_dir / active.id
        bundle = load_bundle(root, active.bundle_sha256)
        assert bundle.data_kind == "real"
        with Session(engine) as session, session.begin():
            import_bundle(session, root, bundle)
        request = ReplayRequest(scenario_id=active.id)
        with Session(engine) as session:
            job, _ = enqueue(
                session,
                settings,
                JobType.REPLAY,
                "acceptance-" + uuid4().hex,
                request.model_dump(mode="json"),
                datetime.now(UTC),
                lambda: replay_payload(session, settings, request, datetime.now(UTC)),
            )
        with Session(engine) as session, session.begin():
            lease = claim(session, settings, datetime.now(UTC), job_id=job.id)
            assert lease and lease.attempt == 1
        environment = dict(os.environ)
        environment["FIREWATCH_ACCEPTANCE_DB"] = url.render_as_string(hide_password=False)
        environment["FIREWATCH_ACCEPTANCE_SCHEMA"] = schema
        environment["FIREWATCH_ACCEPTANCE_JOB"] = str(job.id)

        def invoke(kind):
            return subprocess.run(
                [sys.executable, "-B", str(Path(__file__).resolve()), "--child", kind],
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )

        crash = invoke("crash")
        if crash.returncode != 23:
            raise RuntimeError("Acceptance child failed before its termination checkpoint")
        with Session(engine) as session, session.begin():
            interrupted = session.get(Job, job.id)
            run = session.get(Run, interrupted.result_run_id)
            assert run.status == "running" and run.stage_status["features"] == "succeeded"
            assert session.scalar(select(Alert.id).limit(1)) is None
            interrupted.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        recovered = invoke("recover")
        if recovered.returncode:
            raise RuntimeError("Isolated recovery/handoff child failed")
        result = json.loads(recovered.stdout)
        result.update(
            schema_version="phase-3.7-smoke-v1",
            data_kind="real-cached-historical",
            actual_process_termination_exit=23,
            database_scope="disposable schema in firewatch_test",
            note="Lease expiry accelerated only in the disposable acceptance schema.",
        )
        Path("docs/phase-3.7-smoke.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "recovered_attempts",
                        "network_attempts",
                        "actual_process_termination_exit",
                        "duplicate_alerts",
                    )
                }
            )
        )
    finally:
        engine.dispose()
        with control.begin() as connection:
            assert schema.startswith("fw_accept_") and len(schema) == 42
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
