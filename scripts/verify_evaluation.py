"""Audit the real no-data evaluation receipt without consuming a final-test claim."""

import json
from pathlib import Path

from firewatch.ml.evaluation_package import verify_evaluation

ROOT = Path("data/evaluations/phase-4.5-final")
MANIFEST_SHA256 = "4e002768fc7a1b73e0331587e6c44ae3eb70890840e6883a60072473166aa12a"


def main() -> None:
    verified = verify_evaluation(ROOT, MANIFEST_SHA256)
    report = json.loads((ROOT / "evaluation-report.json").read_bytes())
    assert verified["status"] == "insufficient_data"
    assert verified["final_test_status"] == "sealed"
    assert report["thresholds"] == {} and report["development"] is None
    assert json.loads((ROOT / "evaluation-models.json").read_bytes()) is None
    assert not report["promotion_performed"] and not report["final_test_evaluated"]
    assert not list(ROOT.glob("*.svg"))
    ledger = Path("data/evaluations/final-test-ledger.sqlite3")
    receipt = dict(
        schema_version="phase-4.5-local-acceptance-v1",
        **verified,
        source_training_manifest_sha256="07596df56cbc16e2567ec35ff54f6ef5a82263c3a61d1c0d31d0a728d5f0189d",
        diagnostic_snapshots=12,
        eligible_evaluation_rows=0,
        selected_ml_thresholds=False,
        held_out_metrics=None,
        ablations_evaluated=False,
        reliability_plot_produced=False,
        final_test_evaluated=False,
        authoritative_ledger_exists=ledger.exists(),
        release_recommendation="baseline_only",
        limitations=[
            "All twelve current snapshots are off the frozen six-hour cadence",
            "No supported real held-out metrics or priority-threshold validation",
            "Whole-event synthetic tests validate implementation, not real performance",
            "Final-test ledger must remain authoritative across operators and machines",
        ],
    )
    Path("docs/phase-4.5-smoke.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
