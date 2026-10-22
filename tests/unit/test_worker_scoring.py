"""FRP baseline thresholds and deterministic canonical evidence bytes."""

from datetime import UTC, datetime

import pytest
from feature_factory import inputs, observation

from firewatch.evidence.canonical import canonical_bytes, evidence_hash
from firewatch.ml.predict import score_features
from firewatch.processing.features import build_features


@pytest.mark.parametrize(
    "frp,priority",
    [(0, "LOW"), (9.9, "LOW"), (10, "MEDIUM"), (50, "HIGH"), (100, "CRITICAL"), (None, "LOW")],
)
def test_baseline_thresholds_and_missing_values(frp, priority):
    args = inputs([observation(1, frp=frp)])
    features = build_features(*args)
    result = score_features(features, "baseline-v1", scored_at=datetime.now(UTC))[0]
    assert result.priority == priority and result.escalation_probability is None
    assert result.priority_score == (frp or 0)
    assert result.explanation["frp_missing"] == (frp is None)
    assert result.explanation["feature_hash"] == features[0].feature_hash


def test_no_silent_model_fallback():
    with pytest.raises(ValueError, match="Only baseline"):
        score_features([], "unknown-model-v1")


def test_canonical_order_tamper_and_nonfinite():
    assert canonical_bytes({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert evidence_hash(canonical_bytes({"a": 1})) != evidence_hash(canonical_bytes({"a": 2}))
    with pytest.raises(ValueError):
        canonical_bytes({"a": float("nan")})
