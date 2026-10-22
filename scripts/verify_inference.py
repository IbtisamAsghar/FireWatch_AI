"""Verify real baseline inference while unsupported supervised export fails closed."""

import json
import math
from pathlib import Path

from firewatch.ml.forest_package import read_forest_data
from firewatch.ml.inference_bundle import export_inference_bundle
from firewatch.ml.predict import score_features

ROOT = Path("data/evaluations/phase-4.5-final")
SHA256 = "4e002768fc7a1b73e0331587e6c44ae3eb70890840e6883a60072473166aa12a"


def main() -> None:
    destination = Path("data/models/phase-4.6-inference")
    if destination.exists():
        raise ValueError("Real diagnostic expects no existing candidate at its reserved output")
    refused = False
    try:
        export_inference_bundle(ROOT, destination, SHA256, "forest-candidate-v1")
    except ValueError as error:
        if "No eligible forest/thresholds" not in str(error):
            raise
        refused = True
    assert refused and not destination.exists()
    data = read_forest_data(ROOT / "training/split")
    features = [row.target.feature for row in data.samples]
    assert len(features) == 12
    counts = {}
    for name in ("baseline-v1", "weighted-baseline-v1"):
        scores = score_features(features, name)
        assert len(scores) == 12
        for result in scores:
            assert result.escalation_probability is None and result.model_id is None
            assert result.score_kind == "baseline"
            assert result.warnings == result.explanation["warnings"]
            assert math.isclose(
                sum(result.explanation["contributions"].values()),
                result.priority_score,
                abs_tol=1e-10,
            )
        counts[name] = len(scores)
    receipt = dict(
        schema_version="phase-4.6-local-acceptance-v1",
        source_evaluation_manifest_sha256=SHA256,
        diagnostic_snapshots=12,
        baseline_scored_counts=counts,
        real_supervised_artifact_available=False,
        candidate_export_refused=True,
        approved_model_activated=False,
        final_test_status="sealed",
        real_ml_metrics=None,
        actual_baseline_terms_verified=True,
        warnings_preserved=True,
        limitations=[
            "All current snapshots are off the supervised six-hour cadence",
            "No real fitted forest, ML thresholds or approved model exists",
            "Synthetic SHAP tests establish implementation behavior, not operational validity",
            "Registry promotion and worker approval resolution remain Phase 4.7",
        ],
    )
    Path("docs/phase-4.6-smoke.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
