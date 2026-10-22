"""Versioned artifact-free ranking policies; neither policy estimates probability."""

import math
from typing import Literal, Self

import numpy as np
from pydantic import ConfigDict, Field, model_validator

from firewatch.ml.splits import SplitResult, SplitSample
from firewatch.processing.features import FeatureValues, validate_feature_set
from firewatch.schemas.common import DTO, Digest
from firewatch.schemas.contracts import FeatureSet
from firewatch.sources.firms_normalize import digest

BASELINE_VERSIONS = ("baseline-v1", "weighted-baseline-v1")
COMPONENTS = ("intensity", "dryness", "wind", "previous_detections")
WEIGHTS = (0.40, 0.25, 0.20, 0.15)


class WeightedPolicy(DTO):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["weighted-index-v1"] = "weighted-index-v1"
    weights: tuple[float, float, float, float] = WEIGHTS
    frp_reference_mw: float = Field(default=100, gt=0, le=1e308)
    wind_reference_ms: float = Field(default=15, gt=0, le=1e308)
    previous_reference: float = Field(default=20, gt=0, le=1e308)
    freeze_basis: Literal["prespecified_prototype", "training_feature_quantiles"] = (
        "prespecified_prototype"
    )
    dataset_sha256: Digest | None = None
    fit_row_ids: list[Digest] = Field(default_factory=list)

    @model_validator(mode="after")
    def versioned(self) -> Self:
        if self.weights != WEIGHTS:
            raise ValueError("Changed index weights require a new reviewed policy version")
        if self.freeze_basis == "prespecified_prototype":
            if (
                (self.frp_reference_mw, self.wind_reference_ms, self.previous_reference)
                != (100, 15, 20)
                or self.dataset_sha256 is not None
                or self.fit_row_ids
            ):
                raise ValueError("Prototype policy must retain prespecified reference scales")
        elif (
            not self.fit_row_ids
            or self.fit_row_ids != sorted(set(self.fit_row_ids))
            or self.dataset_sha256 is None
        ):
            raise ValueError("Development policy requires exact training rows and dataset hash")
        return self

    @property
    def sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


def components(feature: FeatureSet, policy: WeightedPolicy) -> tuple[dict[str, float], list[str]]:
    policy = WeightedPolicy.model_validate(policy.model_dump())
    values = FeatureValues.model_validate(validate_feature_set(feature).values)
    previous = values.detections_24h - values.detections_6h
    missing = [
        name
        for name, value in zip(
            COMPONENTS,
            (values.frp_max_mw, values.humidity_pct, values.wind_ms, previous),
            strict=True,
        )
        if value is None
    ]
    # Fixed-denominator missing floors preserve missingness; never redistribute weights.
    result = dict(
        intensity=0.0
        if values.frp_max_mw is None
        else min(1.0, math.log1p(values.frp_max_mw) / math.log1p(policy.frp_reference_mw)),
        dryness=0.0 if values.humidity_pct is None else 1.0 - values.humidity_pct / 100,
        wind=0.0 if values.wind_ms is None else min(1.0, values.wind_ms / policy.wind_reference_ms),
        previous_detections=min(1.0, math.log1p(previous) / math.log1p(policy.previous_reference)),
    )
    return result, missing


def weighted_index(feature: FeatureSet, policy: WeightedPolicy | None = None) -> float:
    values, _ = components(feature, policy or WeightedPolicy())
    return min(
        100.0,
        max(0.0, 100 * math.fsum(values[k] * w for k, w in zip(COMPONENTS, WEIGHTS, strict=True))),
    )


def fit_weighted_policy(
    samples: list[SplitSample], result: SplitResult, *, fold_id: str | None = None
) -> WeightedPolicy:
    from firewatch.ml.preprocessing import development_rows

    rows = development_rows(samples, result, role="fit", fold_id=fold_id)
    if not rows:
        raise ValueError("No training data for index reference scales")
    values = [FeatureValues.model_validate(row.target.feature.values) for row in rows]

    def reference(items: list[float], fallback: float) -> float:
        # Linear 90th percentile is fixed in v1 and uses training features only.
        return max(1.0, float(np.quantile(items, 0.90))) if items else fallback

    return WeightedPolicy(
        frp_reference_mw=reference([v.frp_max_mw for v in values if v.frp_max_mw is not None], 100),
        wind_reference_ms=reference([v.wind_ms for v in values if v.wind_ms is not None], 15),
        previous_reference=reference(
            [float(v.detections_24h - v.detections_6h) for v in values], 20
        ),
        freeze_basis="training_feature_quantiles",
        dataset_sha256=result.dataset_sha256,
        fit_row_ids=sorted(row.row_id for row in rows),
    )


def baseline_catalog() -> dict[str, object]:
    """Public frozen policy metadata; built-ins require no filesystem artifact."""
    return dict(
        schema_version="baseline-policies-v1",
        feature_version="features-v1",
        probability_supported=False,
        threshold_policy="provisional-display-only",
        frp=dict(
            scorer_version="baseline-v1",
            input="frp_max_mw",
            units="MW",
            missing_floor=0.0,
            thresholds=[10.0, 50.0, 100.0],
        ),
        weighted=dict(
            scorer_version="weighted-baseline-v1",
            units="index_0_100",
            thresholds=[35.0, 55.0, 75.0],
            policy=WeightedPolicy().model_dump(mode="json"),
            policy_sha256=WeightedPolicy().sha256,
            weights_status="prespecified_prototype_not_optimized",
            previous_activity_window="(T-24h,T-6h]",
        ),
    )
