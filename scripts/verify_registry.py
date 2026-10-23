"""Real promotion preflight refuses insufficient data without altering registry state."""

import json
from pathlib import Path

from sqlalchemy import func, select

from firewatch.config import get_settings
from firewatch.db.base import SCHEMA_REVISION
from firewatch.db.models import ModelActivation, ModelPromotion, ModelVersion
from firewatch.db.session import Database
from firewatch.ml.registry_artifacts import prepare_promotion

SHA256 = "4e002768fc7a1b73e0331587e6c44ae3eb70890840e6883a60072473166aa12a"


def main() -> None:
    settings = get_settings()
    database = Database(settings)

    def counts() -> dict[str, int]:
        with database.session() as session:
            return {
                kind.__tablename__: int(session.scalar(select(func.count()).select_from(kind)) or 0)
                for kind in (ModelVersion, ModelPromotion, ModelActivation)
            }

    try:
        before = counts()
        refused = False
        try:
            prepare_promotion(
                settings.model_dir,
                "forest-real-preflight-v1",
                Path("data/evaluations/phase-4.5-final"),
                SHA256,
                Path("data/models/phase-4.6-inference"),
                "0" * 64,
                Path(".local/no-real-approval-review.json"),
            )
        except ValueError as error:
            if "completed final-test" not in str(error):
                raise
            refused = True
        after = counts()
        assert refused and before == after
        assert not Path("data/evaluations/final-test-ledger.sqlite3").exists()
        receipt = dict(
            schema_version="phase-4.7-local-acceptance-v1",
            schema_revision=SCHEMA_REVISION,
            source_evaluation_manifest_sha256=SHA256,
            promotion_preflight_refused=True,
            registry_before=before,
            registry_after=after,
            active_scorer=settings.active_scorer,
            real_model_approved=False,
            real_model_activated=False,
            final_test_status="sealed",
            real_ml_metrics=None,
            failure_policy="fail_run_without_fallback",
            required_ml_observations=["frp_max_mw", "frp_sum_mw"],
            limitations=[
                "Current twelve snapshots are off the frozen supervised cadence",
                "No eligible fitted forest or final-test evidence exists",
                "Independent human approval is still required for future eligible releases",
            ],
        )
        Path("docs/phase-4.7-smoke.json").write_text(
            json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(receipt))
    finally:
        database.close()


if __name__ == "__main__":
    main()
