"""Artifact-free FRP and weighted baselines for the worker and public read API."""

from datetime import UTC, datetime
from typing import Any

from firewatch.ml.baselines import (
    BASELINE_VERSIONS,
    COMPONENTS,
    WEIGHTS,
    WeightedPolicy,
    components,
    weighted_index,
)
from firewatch.processing.features import FeatureValues, validate_feature_set
from firewatch.schemas.common import Priority, ScoreKind
from firewatch.schemas.contracts import FeatureSet, ScoreResult


def score_features(
    feature_sets: list[FeatureSet], scorer_version: str, *, scored_at: datetime | None = None
) -> list[ScoreResult]:
    if scorer_version not in BASELINE_VERSIONS:
        raise ValueError(
            "Only baseline-v1 and weighted-baseline-v1 are supported by this worker release"
        )
    results = []
    for feature in feature_sets:
        feature = validate_feature_set(feature)
        frp = FeatureValues.model_validate(feature.values).frp_max_mw
        value = float(frp) if frp is not None else 0.0
        priority = (
            Priority.CRITICAL
            if value >= 100
            else Priority.HIGH
            if value >= 50
            else Priority.MEDIUM
            if value >= 10
            else Priority.LOW
        )
        reason = (
            f"Maximum observed FRP {value:g} MW determines this FRP-only ranking."
            if frp is not None
            else "FRP is missing; ranking floor used with missing-data flag."
        )
        explanation: dict[str, Any] = {
            "reasons": [reason],
            "feature_hash": feature.feature_hash,
            "method": "frp-maximum-only-v1",
            "frp_missing": frp is None,
        }
        missing_flags = []
        if scorer_version == "weighted-baseline-v1":
            policy = WeightedPolicy()
            terms, missing = components(feature, policy)
            contributions = {
                k: 100 * terms[k] * w for k, w in zip(COMPONENTS, WEIGHTS, strict=True)
            }
            value = weighted_index(feature, policy)
            priority = (
                Priority.CRITICAL
                if value >= 75
                else Priority.HIGH
                if value >= 55
                else Priority.MEDIUM
                if value >= 35
                else Priority.LOW
            )
            reason = (
                "Weighted intensity, dryness, wind and prior activity "
                "determine this provisional index."
            )
            missing_flags = ["weighted_index_missing_" + name for name in missing]
            explanation = {
                "reasons": [reason],
                "feature_hash": feature.feature_hash,
                "method": "weighted-index-v1",
                "frp_missing": frp is None,
                "policy": policy.model_dump(mode="json"),
                "policy_sha256": policy.sha256,
                "components": terms,
                "contributions": contributions,
                "missing_components": missing,
                "missing_policy": "fixed_weight_zero_floor",
                "threshold_policy": "provisional-display-only",
                "thresholds": [35.0, 55.0, 75.0],
                "score_units": "index_0_100",
            }
        results.append(
            ScoreResult(
                run_id=feature.run_id,
                mode=feature.mode,
                as_of=feature.as_of,
                feature_version=feature.feature_version,
                snapshot_id=feature.snapshot_id,
                scorer_version=scorer_version,
                priority_score=value,
                score_kind=ScoreKind.BASELINE,
                escalation_probability=None,
                priority=priority,
                reasons=[reason],
                scored_at=scored_at or datetime.now(UTC),
                explanation=explanation,
                quality_flags=missing_flags,
            )
        )
    return results
