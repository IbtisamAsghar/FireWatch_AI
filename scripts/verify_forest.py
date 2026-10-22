"""Offline real Phase 4.4 receipt; never invent an estimator for an ineligible cohort."""

import json
from pathlib import Path

from firewatch.ml.forest_package import read_forest_data, verify_forest
from firewatch.ml.forest_spec import ForestSpec

ROOT = Path("data/models/phase-4.4-final")
MANIFEST_SHA256 = "07596df56cbc16e2567ec35ff54f6ef5a82263c3a61d1c0d31d0a728d5f0189d"


def main() -> None:
    verified = verify_forest(ROOT, MANIFEST_SHA256)
    data = read_forest_data(ROOT / "split")
    assert not verified["model_available"] and not verified["probability_supported"]
    assert (ROOT / "model.json").read_text(encoding="utf-8").strip() == "null"
    receipt = dict(
        schema_version="phase-4.4-local-acceptance-v1",
        **verified,
        source_split_manifest_sha256="cca85bb36cc4a207a152b38686f685de961a8b6ebb9c9ddaa807547d17d08264",
        diagnostic_snapshots=len(data.samples),
        partition_counts=data.result.partition_counts,
        exclusion_counts=data.result.exclusion_counts,
        configured_candidate_count=len(ForestSpec().candidates()),
        fitted_candidate_count=0,
        model_fitted=False,
        calibrator_fitted=False,
        final_test_evaluated=False,
        model_promoted=False,
        executable_serialization=False,
        held_out_metrics=None,
        release_recommendation="baseline_only",
        limitations=[
            "All twelve current snapshots are off the frozen six-hour cadence",
            "No eligible real Random Forest or calibration fit is supported",
            "Synthetic tests exercise training and calibration without claiming real performance",
            (
                "Development calibration gates do not establish operational validity "
                "or release approval"
            ),
        ],
    )
    Path("docs/phase-4.4-smoke.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
