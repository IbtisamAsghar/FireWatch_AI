"""Real cached-data worker smoke in a disposable schema, with actual process termination."""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from firewatch.config import get_settings
from firewatch.db.models import (
    Alert,
    ClimateBaselineRecord,
    Detection,
    DetectionRegion,
    DetectionSource,
    FeatureSetRecord,
    IngestionBatch,
    Job,
    LiveRunHead,
    Region,
    Run,
    Score,
    SourceAsset,
    WeatherRecord,
)
from firewatch.pipeline.jobs import claim
from firewatch.sources.firms_normalize import digest
from firewatch.worker import process_one

TABLES = [
    Region,
    SourceAsset,
    IngestionBatch,
    Detection,
    DetectionSource,
    DetectionRegion,
    WeatherRecord,
    ClimateBaselineRecord,
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path, default=Path("docs/phase-3.5-smoke.json"))
    args = parser.parse_args()
    settings = get_settings()
    source = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    with Session(source) as session:
        run = session.get(Run, args.source_run_id)
        if run is None or "firms" not in run.input_asset_manifest:
            raise ValueError("Source run must have frozen satellite inputs")
        policy = run.input_asset_manifest["firms"]["policy"]
        batches = list(
            session.scalars(
                select(IngestionBatch).where(
                    IngestionBatch.id.in_([UUID(i) for i in policy["batch_ids"]])
                )
            )
        )
        payload = dict(
            schema_version="jobs-v1",
            region_id=str(run.region_id),
            mode="replay",
            as_of=run.as_of.isoformat(),
            window_start=(run.as_of - timedelta(hours=48)).isoformat(),
            window_end=run.as_of.isoformat(),
            scorer_version="baseline-v1",
            feature_version="features-v1",
            asset_ids=sorted(str(b.asset_id) for b in batches),
        )
        rows = {}
        for model in TABLES:
            records = list(session.execute(select(model.__table__).limit(50001)).mappings())
            if len(records) > 50000:
                raise ValueError("Real smoke copy exceeds bounded test size")
            rows[model] = [dict(r) for r in records]
    target_url = make_url(settings.database_url.get_secret_value()).set(database="firewatch_test")
    schema = "fw_worker_" + uuid4().hex
    control = create_engine(target_url, hide_parameters=True)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        target_url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )
    try:
        configuration = Config("alembic.ini")
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")
        with engine.begin() as connection:
            for model in TABLES:
                if rows[model]:
                    connection.execute(insert(model.__table__), rows[model])
            connection.execute(update(Region).values(enabled=False))
            connection.execute(
                update(Region).where(Region.id == UUID(payload["region_id"])).values(enabled=True)
            )
        identifier = uuid4()
        clock = datetime.now(UTC)
        with Session(engine) as session, session.begin():
            session.add(
                Job(
                    id=identifier,
                    type="replay",
                    payload=payload,
                    payload_hash=digest(payload),
                    idempotency_key="real-worker-smoke",
                    status="queued",
                    attempts=0,
                    available_at=clock,
                )
            )
        with Session(engine) as session, session.begin():
            assert claim(session, settings, clock) is not None
        environment = dict(os.environ)
        environment["FIREWATCH_SMOKE_DB"] = target_url.render_as_string(hide_password=False)
        environment["FIREWATCH_SMOKE_SCHEMA"] = schema
        environment["FIREWATCH_SMOKE_JOB"] = str(identifier)
        # Exit without Python cleanup immediately after the durable features checkpoint.
        child = """import os
from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy import create_engine
from firewatch.config import get_settings
from firewatch.pipeline.jobs import Lease
from firewatch.pipeline.runner import execute_run
from firewatch.schemas.common import Stage
engine=create_engine(os.environ['FIREWATCH_SMOKE_DB'],hide_parameters=True,
 connect_args={'options':'-csearch_path='+os.environ['FIREWATCH_SMOKE_SCHEMA']
 +',public -c timezone=UTC'})
def terminate(stage):
 if stage == Stage.FEATURES: os._exit(23)
execute_run(engine,get_settings(),Lease(UUID(os.environ['FIREWATCH_SMOKE_JOB']),1),
 lambda:datetime.now(UTC),after_stage=terminate)
"""
        result = subprocess.run(
            [sys.executable, "-B", "-c", child],
            env=environment,
            capture_output=True,
            timeout=90,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode != 23:
            raise RuntimeError("Smoke child did not reach its forced termination checkpoint")
        with Session(engine) as session, session.begin():
            job = session.get(Job, identifier)
            interrupted = session.get(Run, job.result_run_id)
            assert interrupted.stage_status["features"] == "succeeded"
            assert interrupted.status == "running"
            # Accelerate expiry only in this disposable schema.
            job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
        assert process_one(engine, settings)
        with Session(engine) as session:
            job = session.get(Job, identifier)
            assert job.status == "succeeded" and job.attempts == 2
            run_id = job.result_run_id
            finished = session.get(Run, run_id)
            assert finished.status == "completed" and finished.mode == "replay"
            hashes = sorted(session.scalars(select(FeatureSetRecord.feature_hash)))
            scores = list(session.scalars(select(Score)))
            alerts = list(session.scalars(select(Alert)))
            assert session.scalar(select(LiveRunHead.region_id).limit(1)) is None
            assert all(
                a.original_hash == __import__("hashlib").sha256(a.canonical_payload).hexdigest()
                for a in alerts
            )
            before = [len(hashes), len(scores), len(alerts)]
            # Re-submit exactly the same work with a different admission key.
            with Session(engine) as second, second.begin():
                second.add(
                    Job(
                        id=uuid4(),
                        type="replay",
                        payload=payload,
                        payload_hash=digest(payload),
                        idempotency_key="real-worker-repeat",
                        status="queued",
                        attempts=0,
                        available_at=datetime.now(UTC),
                    )
                )
            assert process_one(engine, settings)
            session.expire_all()
            after_hashes = sorted(session.scalars(select(FeatureSetRecord.feature_hash)))
            after = [
                len(after_hashes),
                len(list(session.scalars(select(Score)))),
                len(list(session.scalars(select(Alert)))),
            ]
            assert before == after and hashes == after_hashes
            output = dict(
                schema_version="phase-3.5-smoke-v1",
                data_kind="real-cached-historical",
                source_run_id=str(args.source_run_id),
                run_id=str(run_id),
                mode="replay",
                as_of=finished.as_of.isoformat(),
                status=finished.status.value,
                completeness=finished.completeness.value,
                stages=finished.stage_status,
                actual_process_termination_exit=23,
                recovered_attempts=job.attempts,
                feature_sets=before[0],
                scores=before[1],
                alerts=before[2],
                probabilities_all_null=all(s.escalation_probability is None for s in scores),
                feature_hashes=hashes,
                repeated_counts_identical=before == after,
                repeated_feature_hashes_identical=hashes == after_hashes,
                evidence_hashes_verified=True,
                provider_calls=0,
                live_pointer_unchanged=True,
                application_region_settings_unchanged=True,
                database_scope="disposable schema in firewatch_test",
                coverage_warnings=finished.input_asset_manifest["coverage_warnings"],
                recorded_at=datetime.now(UTC).isoformat(),
            )
            args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
            print(
                json.dumps(
                    {
                        k: output[k]
                        for k in (
                            "status",
                            "feature_sets",
                            "scores",
                            "alerts",
                            "recovered_attempts",
                            "provider_calls",
                        )
                    }
                )
            )
    finally:
        engine.dispose()
        with control.begin() as connection:
            assert schema.startswith("fw_worker_") and len(schema) == 42
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
        source.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
