"""Build and verify the real Phase 4.8 baseline handoff, without DB or final access."""

import json
from pathlib import Path

from firewatch.ml.acceptance import export_baseline_release, verify_baseline_release

EVALUATION = Path("data/evaluations/phase-4.5-final")
EVALUATION_SHA = "4e002768fc7a1b73e0331587e6c44ae3eb70890840e6883a60072473166aa12a"
COMPARISON = Path("data/baselines/phase-4.3-final")
COMPARISON_SHA = "2d50d21aa2d7ab4585a512f7a1e080f7e496ea65526dfc4eb5550da77e4ec180"
OUTPUT = Path("data/handoffs/module-4-v1")


def main() -> None:
    ledger = Path("data/evaluations/final-test-ledger.sqlite3")
    if ledger.exists():
        raise ValueError("Current baseline receipt requires the real final test to remain sealed")
    if not OUTPUT.exists():
        export_baseline_release(EVALUATION, COMPARISON, OUTPUT, EVALUATION_SHA, COMPARISON_SHA)
    verified = verify_baseline_release(OUTPUT)
    manifest = json.loads((OUTPUT / "manifest.json").read_bytes())
    if (
        manifest["evaluation_manifest_sha256"] != EVALUATION_SHA
        or manifest["comparison_manifest_sha256"] != COMPARISON_SHA
        or verified["diagnostic_rows"] != 12
        or ledger.exists()
    ):
        raise ValueError("Baseline handoff differs from reviewed real evidence")
    report = json.loads((OUTPUT / "release-report.json").read_bytes())
    if report["exclusion_counts"].get("off_six_hour_cadence") != 12:
        raise ValueError("Real data eligibility changed; review a new release")
    receipt = dict(
        schema_version="phase-4.8-local-acceptance-v1",
        **verified,
        source_evaluation_manifest_sha256=EVALUATION_SHA,
        source_comparison_manifest_sha256=COMPARISON_SHA,
        recommended_scorer="baseline-v1",
        eligible_supervised_rows=0,
        held_out_ml_metrics=None,
        ml_probability=None,
        approved_bundle_status="unavailable_insufficient_data",
        final_test_evaluated=False,
        real_model_approved=False,
        real_model_activated=False,
        private_handoff_path=OUTPUT.as_posix(),
        public_evidence_scope="Hashes and counts; source data remains private and ignored by Git",
    )
    Path("docs/phase-4.8-smoke.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
