"""Verify Phase 5.1 on real cached replays in a disposable PostgreSQL schema."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from firewatch.config import get_settings
from firewatch.db.models import (
    Alert,
    AnchorOutbox,
    Job,
    LiveRunHead,
    ModelActivation,
    ModelPromotion,
    ModelVersion,
    Region,
    Run,
    Score,
)
from firewatch.evidence.serialization import CANONICALIZATION_VERSION
from firewatch.pipeline.replay import replay_scenario
from scripts.verify_replays import isolated_engine


def application_state(engine):
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        alerts = list(session.scalars(select(Alert)))
        return dict(
            counts={
                model.__tablename__: session.scalar(select(func.count()).select_from(model))
                for model in (
                    Alert,
                    AnchorOutbox,
                    Run,
                    Score,
                    Job,
                    LiveRunHead,
                    ModelVersion,
                    ModelPromotion,
                    ModelActivation,
                )
            },
            alerts={
                str(a.id): [hashlib.sha256(a.canonical_payload).hexdigest(), a.original_hash]
                for a in alerts
            },
            regions=sorted((str(r.id), r.enabled) for r in session.scalars(select(Region))),
        )


def main() -> int:
    settings = get_settings().model_copy(
        update={"active_scorer": "baseline-v1", "chain_enabled": False}
    )
    source = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        before = application_state(source)
        attempts = []

        def denied(*args, **kwargs):
            attempts.append(True)
            raise RuntimeError("Provider I/O denied during cached evidence acceptance")

        results = []
        with isolated_engine(settings) as engine, patch("httpx.Client.send", denied):
            for name in ("norcal-active-v1", "norcal-quiet-v1", "norcal-degraded-v1"):
                first = replay_scenario(engine, settings, name)
                with Session(engine) as session:
                    original = {
                        str(a.id): (a.canonical_payload, a.original_hash)
                        for a in session.scalars(select(Alert))
                    }
                second = replay_scenario(engine, settings, name)
                assert first["run_id"] == second["run_id"]
                with Session(engine) as session:
                    assert original == {
                        str(a.id): (a.canonical_payload, a.original_hash)
                        for a in session.scalars(select(Alert))
                    }
                    alerts = list(
                        session.scalars(select(Alert).where(Alert.run_id == UUID(first["run_id"])))
                    )
                    for alert in alerts:
                        wire = json.loads(alert.canonical_payload)
                        assert wire["canonicalization_version"] == CANONICALIZATION_VERSION
                        assert (
                            hashlib.sha256(alert.canonical_payload).hexdigest()
                            == alert.original_hash
                        )
                        assert (
                            hashlib.sha256(wire["feature_payload_json"].encode()).hexdigest()
                            == wire["feature_hash"]
                        )
                        assert isinstance(wire["priority_score"], str)
                        assert wire["escalation_probability"] is None
                    results.append(
                        dict(
                            scenario=name,
                            run_id=first["run_id"],
                            status=first["status"],
                            mode=first["mode"],
                            features=len(first["feature_hashes"]),
                            scores=len(first["priority_scores"]),
                            alerts=len(alerts),
                            exact_saved_hashes_match=True,
                            repeated_bytes_unchanged=True,
                            upstream_feature_preimages_match=True,
                        )
                    )
            with Session(engine) as session:
                assert session.scalar(select(func.count()).select_from(LiveRunHead)) == 0
                assert session.scalar(select(func.count()).select_from(AnchorOutbox)) == 0
                assert not any(session.scalars(select(Region.enabled)))
        assert not attempts
        assert application_state(source) == before
        output = dict(
            schema_version="phase-5.1-acceptance-v1",
            recorded_at=datetime.now(UTC).isoformat(),
            canonicalization_version=CANONICALIZATION_VERSION,
            data_kind="real-cached-historical",
            database_scope="disposable migrated test schema",
            provider_attempts=0,
            chain_calls=0,
            application_state_unchanged=True,
            historical_alert_bytes_unchanged=True,
            scenarios=results,
        )
        Path("docs/phase-5.1-smoke.json").write_text(
            json.dumps(output, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                dict(
                    verified=True,
                    scenarios=len(results),
                    alerts=sum(r["alerts"] for r in results),
                    provider_attempts=0,
                    application_state_unchanged=True,
                )
            )
        )
    finally:
        source.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
