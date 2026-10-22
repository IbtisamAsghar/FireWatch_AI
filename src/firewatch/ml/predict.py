"""Frozen FRP-only baseline needed by the first complete worker pipeline."""

from datetime import UTC, datetime

from firewatch.processing.features import FeatureValues, validate_feature_set
from firewatch.schemas.common import Priority, ScoreKind
from firewatch.schemas.contracts import FeatureSet, ScoreResult


def score_features(
    feature_sets: list[FeatureSet], scorer_version: str, *, scored_at: datetime | None = None
) -> list[ScoreResult]:
    if scorer_version != "baseline-v1":
        raise ValueError("Only baseline-v1 is supported by this worker release")
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
                explanation={
                    "reasons": [reason],
                    "feature_hash": feature.feature_hash,
                    "method": "frp-maximum-only-v1",
                    "frp_missing": frp is None,
                },
            )
        )
    return results
