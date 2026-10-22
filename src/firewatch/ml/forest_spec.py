"""Frozen, bounded Random Forest development-training policy."""

from itertools import product
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import Field, model_validator

from firewatch.schemas.common import DTO
from firewatch.sources.firms_normalize import digest


def _class_weights() -> list[Literal["balanced"] | None]:
    return [None, "balanced"]


class ForestParameters(DTO):
    n_estimators: int = Field(ge=8, le=128)
    max_depth: int = Field(ge=2, le=12)
    min_samples_leaf: int = Field(ge=1, le=20)
    class_weight: Literal["balanced"] | None


class ForestSpec(DTO):
    schema_version: Literal["forest-training-v1"] = "forest-training-v1"
    tree_counts: list[int] = Field(default_factory=lambda: [32, 64], min_length=1, max_length=2)
    depths: list[int] = Field(default_factory=lambda: [4, 8], min_length=1, max_length=2)
    leaf_sizes: list[int] = Field(default_factory=lambda: [1, 3], min_length=1, max_length=2)
    class_weights: list[Literal["balanced"] | None] = Field(
        default_factory=_class_weights, min_length=2, max_length=2
    )
    random_seed: Literal[42] = 42
    n_jobs: Literal[1] = 1
    max_features: Literal["sqrt"] = "sqrt"
    calibration_purge_hours: int = Field(default=48, ge=48, le=168)
    min_calibration_events: int = Field(default=4, ge=4, le=100)
    min_calibration_rows_per_class: int = Field(default=10, ge=10, le=1000)
    min_audit_events: int = Field(default=4, ge=4, le=100)
    min_audit_rows_per_class: int = Field(default=10, ge=10, le=1000)
    calibration_split_fraction: float = Field(default=0.5, ge=0.5, le=0.5, strict=True)
    selection_policy: Literal["mean_validation_ap_then_brier_then_complexity"] = (
        "mean_validation_ap_then_brier_then_complexity"
    )
    calibration_acceptance: Literal["nondegrading_audit_brier_and_log_loss"] = (
        "nondegrading_audit_brier_and_log_loss"
    )

    @model_validator(mode="after")
    def bounded(self) -> Self:
        for values in (self.tree_counts, self.depths, self.leaf_sizes):
            if values != sorted(set(values)):
                raise ValueError("Forest grid values must be sorted and unique")
        if self.class_weights != [None, "balanced"]:
            raise ValueError("Compare both unweighted and training-balanced candidates")
        for n, depth, leaf, weight in product(
            self.tree_counts, self.depths, self.leaf_sizes, self.class_weights
        ):
            ForestParameters(
                n_estimators=n, max_depth=depth, min_samples_leaf=leaf, class_weight=weight
            )
        return self

    def candidates(self) -> list[ForestParameters]:
        return [
            ForestParameters(n_estimators=n, max_depth=d, min_samples_leaf=leaf, class_weight=w)
            for n, d, leaf, w in product(
                self.tree_counts, self.depths, self.leaf_sizes, self.class_weights
            )
        ]

    @property
    def sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


def load_forest_spec(path: Path) -> ForestSpec:
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("Oversized forest training specification")
    return ForestSpec.model_validate(yaml.safe_load(raw))
