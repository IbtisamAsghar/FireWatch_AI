"""Deterministic signed explanations in raw forest-vote or baseline score units."""

from typing import Any

from firewatch.processing.features import MODEL_INPUT_NAMES
from firewatch.schemas.contracts import FeatureSet

LABELS = {
    "frp_max_mw": "Maximum observed FRP",
    "frp_sum_mw": "Summed observed FRP",
    "brightness_max_k": "Maximum brightness",
    "detections_6h": "Six-hour detection count",
    "detections_12h": "Twelve-hour detection count",
    "detections_24h": "Daily detection count",
    "hours_since_first_seen_24h": "First-seen age in the daily window",
    "confidence_high_fraction": "High-confidence detection fraction",
    "day_fraction": "Daytime detection fraction",
    "temperature_c": "Air temperature",
    "humidity_pct": "Relative humidity",
    "wind_ms": "Wind speed",
    "gust_ms": "Wind gust",
    "precipitation_24h_mm": "Daily precipitation",
    "temperature_departure_c": "Temperature departure from baseline",
    "rainfall_deficit_24h_mm": "Rainfall deficit relative to baseline",
    "weather_age_hours": "Weather observation age",
}


def forest_explanation(
    feature: FeatureSet,
    contributions: list[float],
    base: float,
    raw_score: float,
    calibrated: bool,
    medians: list[float],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for index, (name, value) in enumerate(zip(MODEL_INPUT_NAMES, contributions, strict=True)):
        is_flag = name.endswith("_missing")
        source = name.removesuffix("_missing")
        missing = feature.values[source] is None
        entries.append(
            dict(
                feature=name,
                contribution=value,
                observed_value=feature.values[source] if not is_flag else missing,
                imputed=missing and not is_flag,
                imputed_value=medians[index] if missing and not is_flag else None,
            )
        )
    reasons: list[str] = []
    for entry in sorted(entries, key=lambda e: (-abs(e["contribution"]), e["feature"])):
        if abs(entry["contribution"]) <= 1e-12 or len(reasons) >= 3:
            continue
        name, value = entry["feature"], entry["contribution"]
        source = name.removesuffix("_missing")
        if name.endswith("_missing"):
            description = LABELS[source] + (
                " missing indicator is set"
                if feature.values[source] is None
                else " missing indicator is not set"
            )
        elif entry["imputed"]:
            description = (
                f"{LABELS[source]} is missing; training-median imputation "
                f"({entry['imputed_value']:g} {feature.units[source]})"
            )
        else:
            description = f"{LABELS[source]} ({feature.values[source]} {feature.units[source]})"
        direction = "raises" if value > 0 else "lowers"
        reasons.append(f"{description} {direction} the raw model score by {abs(value):.6g}.")
    if not reasons:
        reasons = ["No feature contributions exceed the 1e-12 display tolerance."]
    return dict(
        method="exact-tree-path-dependent-shap-v1",
        reasons=reasons,
        feature_hash=feature.feature_hash,
        feature_order=list(MODEL_INPUT_NAMES),
        base_value=base,
        raw_model_score=raw_score,
        contribution_units="raw_positive_class_forest_vote_fraction",
        background="training_weighted_tree_path_covers",
        contributions=entries,
        additivity_target="raw_model_score",
        calibration_note=(
            "Contributions explain the underlying forest ranking; "
            "they do not sum to calibrated probability."
            if calibrated
            else "Uncalibrated model score; escalation probability is null."
        ),
        causal_claim=False,
    )


def weighted_reasons(contributions: dict[str, float], missing: list[str]) -> list[str]:
    labels = {
        "intensity": "Observed FRP intensity",
        "dryness": "Observed humidity-derived dryness",
        "wind": "Observed wind",
        "previous_detections": "Observed prior detection activity",
    }
    reasons: list[str] = []
    for name, term in sorted(contributions.items(), key=lambda item: (-abs(item[1]), item[0])):
        if name in missing:
            reasons.append(
                f"{name.replace('_', ' ').capitalize()} is missing; fixed zero term used."
            )
        else:
            reasons.append(f"{labels[name]} contributes {term:.6g} index points.")
    return reasons[:3]
