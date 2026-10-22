"""Real offline Phase 4.3 receipt; diagnostics are never held-out model metrics."""

import json
from collections import Counter
from pathlib import Path

from firewatch.ml.baselines import WeightedPolicy
from firewatch.ml.comparison_package import verify_comparison
from firewatch.ml.predict import score_features
from firewatch.ml.split_package import read_samples

ROOT = Path("data/baselines/phase-4.3-final")
MANIFEST_SHA256 = "2d50d21aa2d7ab4585a512f7a1e080f7e496ea65526dfc4eb5550da77e4ec180"


def main() -> None:
    verified = verify_comparison(ROOT, MANIFEST_SHA256)
    samples = read_samples(ROOT / "split/labels")
    before = [row.target.feature.model_dump_json() for row in samples]
    a = score_features([r.target.feature for r in samples], "baseline-v1")
    b = score_features([r.target.feature for r in samples], "weighted-baseline-v1")
    assert before == [row.target.feature.model_dump_json() for row in samples]
    assert all(row.escalation_probability is None and row.model_id is None for row in a + b)
    receipt = dict(
        schema_version="phase-4.3-local-acceptance-v1",
        **verified,
        source_split_manifest_sha256="cca85bb36cc4a207a152b38686f685de961a8b6ebb9c9ddaa807547d17d08264",
        diagnostic_snapshots=len(samples),
        diagnostic_only=True,
        diagnostic_priority_counts=dict(
            frp=dict(Counter(row.priority.value for row in a)),
            weighted=dict(Counter(row.priority.value for row in b)),
        ),
        weighted_missing_component_counts=dict(
            Counter(name for row in b for name in row.explanation["missing_components"])
        ),
        weighted_prototype_sha256=WeightedPolicy().sha256,
        weights_status="prespecified_prototype_not_optimized",
        feature_bytes_unchanged=True,
        probability_supported=False,
        final_test_evaluated=False,
        model_promoted=False,
        held_out_metrics=None,
        limitations=[
            "All twelve real snapshots are off cadence; no eligible validation cohort",
            "Diagnostic priorities are provisional rankings, not severity or probability",
            "No real logistic fit or supervised performance is asserted",
        ],
    )
    Path("docs/phase-4.3-smoke.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
