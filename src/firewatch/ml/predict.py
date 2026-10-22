"""Atomic batch scoring with explicit approved ML and artifact-free baseline policies."""

from datetime import UTC, datetime
from threading import Lock
from typing import Any

from firewatch.ml.baselines import (
    BASELINE_VERSIONS,
    COMPONENTS,
    WEIGHTS,
    WeightedPolicy,
    components,
    weighted_index,
)
from firewatch.ml.explain import weighted_reasons
from firewatch.ml.inference_bundle import ApprovedScorer, RuntimeForest, load_bundle
from firewatch.processing.features import FeatureValues, validate_feature_set
from firewatch.schemas.common import Priority, ScoreKind
from firewatch.schemas.contracts import FeatureSet, ScoreResult

_MODEL_CACHE: dict[tuple[str, str, str], RuntimeForest] = {}
_MODEL_LOCK = Lock()


def _approved_runtime(reference: ApprovedScorer) -> RuntimeForest:
    key = (
        str(reference.artifact_path.resolve()),
        reference.manifest_sha256,
        reference.scorer_version,
    )
    # Unlike lru_cache, this lock prevents concurrent first calls loading twice.
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            if len(_MODEL_CACHE) >= 8:
                raise ValueError(
                    "Process scorer cache is full; restart with reviewed active versions"
                )
            _MODEL_CACHE[key] = load_bundle(
                reference.artifact_path, reference.manifest_sha256, reference.scorer_version
            )
        return _MODEL_CACHE[key]


def _warnings(feature: FeatureSet) -> list[str]:
    flags = feature.lineage.get("quality_flags", [])
    return sorted(
        set(
            ([v for v in flags if isinstance(v, str)] if isinstance(flags, list) else [])
            + [name + "_missing" for name, missing in feature.missing_flags.items() if missing]
            + ["priority_thresholds_provisional", "research_proxy_not_physical_fire_severity"]
        )
    )


def score_features(
    feature_sets: list[FeatureSet],
    scorer_version: str,
    *,
    scored_at: datetime | None = None,
    approved: ApprovedScorer | None = None,
) -> list[ScoreResult]:
    """Validate the complete batch, then score in input order without silent fallback.

    ML requires an approval reference from a trusted authority. Candidate packages
    and environment variables cannot approve themselves. Worker registry resolution
    is introduced separately in Phase 4.7; baseline workers remain explicitly gated.
    """
    if len(feature_sets) > 256:
        raise ValueError("Scoring batch exceeds the 256-snapshot limit")
    features = [validate_feature_set(feature) for feature in feature_sets]
    timestamp = scored_at or datetime.now(UTC)
    if scorer_version not in BASELINE_VERSIONS:
        if approved is None:
            raise ValueError("Supervised scoring requires an approved trusted artifact reference")
        reference = ApprovedScorer.model_validate(approved.model_dump())
        if reference.scorer_version != scorer_version:
            raise ValueError("Requested scorer differs from the approved version")
        if not features:
            return []
        runtime = _approved_runtime(reference)
        results = []
        for feature in features:
            ranking, probability, priority, explanation = runtime.score(feature)
            warnings = _warnings(feature) + (
                ["uncalibrated_model_score_probability_unavailable"]
                if probability is None
                else ["shap_explains_raw_model_score_not_calibrated_probability"]
            )
            explanation.update(
                warnings=warnings,
                inference_manifest_sha256=reference.manifest_sha256,
                approval_reference=reference.approval_reference,
            )
            results.append(
                ScoreResult(
                    run_id=feature.run_id,
                    mode=feature.mode,
                    as_of=feature.as_of,
                    feature_version=feature.feature_version,
                    snapshot_id=feature.snapshot_id,
                    scorer_version=scorer_version,
                    model_id=reference.model_id,
                    priority_score=ranking,
                    escalation_probability=probability,
                    score_kind=ScoreKind.PROBABILITY
                    if probability is not None
                    else ScoreKind.MODEL,
                    priority=Priority(priority),
                    reasons=explanation["reasons"],
                    explanation=explanation,
                    warnings=warnings,
                    quality_flags=warnings,
                    scored_at=timestamp,
                )
            )
        return results
    if approved is not None:
        raise ValueError("Baseline scoring must not attach a supervised approval reference")
    return _score_baselines(features, scorer_version, timestamp)


def _score_baselines(
    feature_sets: list[FeatureSet],
    scorer_version: str,
    timestamp: datetime,
) -> list[ScoreResult]:
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
            "base_value": 0.0,
            "contributions": {"frp_max_mw": value},
            "contribution_units": "MW",
            "threshold_policy": "provisional-display-only",
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
                "contribution_units": "index_points",
                "base_value": 0.0,
            }
        reasons = (
            weighted_reasons(contributions, missing)
            if scorer_version == "weighted-baseline-v1"
            else [reason]
        )
        warnings = sorted(set(_warnings(feature) + missing_flags))
        explanation.update(reasons=reasons, warnings=warnings)
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
                reasons=reasons,
                scored_at=timestamp,
                explanation=explanation,
                quality_flags=warnings,
                warnings=warnings,
            )
        )
    return results
