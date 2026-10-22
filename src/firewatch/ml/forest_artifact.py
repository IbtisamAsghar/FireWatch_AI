"""Non-executable JSON representation of binary forests and their preprocessing."""

import math
from typing import Literal, Self
from uuid import UUID

import numpy as np
from pydantic import Field, model_validator

from firewatch.ml.forest_spec import ForestParameters
from firewatch.ml.labels import TargetSpec
from firewatch.processing.features import (
    FEATURE_NAMES,
    MODEL_INPUT_NAMES,
    FeatureSpec,
    model_inputs,
)
from firewatch.schemas.common import DTO, Digest
from firewatch.schemas.contracts import FeatureSet
from firewatch.sources.firms_normalize import digest


class NumericTransform(DTO):
    medians: list[float] = Field(min_length=17, max_length=17)
    means: list[float] = Field(min_length=17, max_length=17)
    scales: list[float] = Field(min_length=17, max_length=17)

    @model_validator(mode="after")
    def positive_scale(self) -> Self:
        if any(s <= 0 for s in self.scales):
            raise ValueError("Preprocessing scale must be positive")
        return self


class ForestNode(DTO):
    left: int
    right: int
    feature: int
    threshold: float
    class_values: tuple[float, float]
    impurity: float = Field(ge=0)
    samples: int = Field(ge=1)
    weighted_samples: float = Field(gt=0)

    @model_validator(mode="after")
    def probabilities(self) -> Self:
        if min(self.class_values) < 0 or not math.isclose(
            sum(self.class_values), 1.0, abs_tol=1e-10
        ):
            raise ValueError("Tree class values must be normalized binary fractions")
        return self


class ForestTree(DTO):
    nodes: list[ForestNode] = Field(min_length=1, max_length=8191)

    @model_validator(mode="after")
    def graph(self) -> Self:
        seen = set()
        pending = [(0, 0)]
        while pending:
            index, depth = pending.pop()
            if index in seen or not 0 <= index < len(self.nodes) or depth > 12:
                raise ValueError("Invalid/cyclic/oversized forest tree")
            seen.add(index)
            node = self.nodes[index]
            if node.left == node.right == -1:
                if node.feature != -2:
                    raise ValueError("Leaf cannot inspect a model feature")
            elif (
                node.left <= index
                or node.right <= index
                or node.left == node.right
                or not 0 <= node.feature < len(MODEL_INPUT_NAMES)
            ):
                raise ValueError("Invalid tree branch or feature index")
            else:
                pending.extend([(node.left, depth + 1), (node.right, depth + 1)])
        if len(seen) != len(self.nodes):
            raise ValueError("Unreachable forest nodes")
        return self


class Sigmoid(DTO):
    a: float = Field(ge=-1e6, lt=0)
    b: float = Field(ge=-1e6, le=1e6)

    def apply(self, value: float) -> float:
        z = -(self.a * value + self.b)
        return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


class ForestPrediction(DTO):
    snapshot_id: UUID
    feature_hash: Digest
    model_score: float = Field(ge=0, le=1)
    escalation_probability: float | None = Field(default=None, ge=0, le=1)
    score_kind: Literal["model_score", "calibrated_probability"]


class PortableForest(DTO):
    schema_version: Literal["portable-binary-forest-v1"] = "portable-binary-forest-v1"
    class_order: tuple[Literal[0], Literal[1]] = (0, 1)
    preprocessing_algorithm: Literal["median_scale_17_passthrough_14"] = (
        "median_scale_17_passthrough_14"
    )
    feature_names: list[str]
    feature_spec_sha256: Digest
    target_spec_sha256: Digest
    dataset_sha256: Digest
    split_spec_sha256: Digest
    training_spec_sha256: Digest
    parameters: ForestParameters
    fit_row_ids: list[Digest] = Field(min_length=1, max_length=10000)
    fold_id: str | None
    numeric: NumericTransform
    trees: list[ForestTree] = Field(min_length=8, max_length=128)
    sigmoid: Sigmoid | None = None
    calibration_status: Literal["accepted", "rejected", "insufficient_data", "cv_uncalibrated"]
    calibration_fit_ids: list[Digest] = Field(default_factory=list, max_length=10000)
    calibration_audit_ids: list[Digest] = Field(default_factory=list, max_length=10000)
    probability_supported: bool

    @model_validator(mode="after")
    def contract(self) -> Self:
        if (
            self.feature_spec_sha256 != digest(FeatureSpec().model_dump(mode="json"))
            or self.target_spec_sha256 != TargetSpec().sha256
            or sum(len(t.nodes) for t in self.trees) > 100000
        ):
            raise ValueError("Forest frozen definitions or node budget mismatch")
        if (
            self.feature_names != list(MODEL_INPUT_NAMES)
            or len(self.trees) != self.parameters.n_estimators
        ):
            raise ValueError("Forest feature whitelist or estimator count mismatch")
        if self.probability_supported != (
            self.calibration_status == "accepted"
        ) or self.probability_supported != (self.sigmoid is not None):
            raise ValueError("Unsupported probability claim")
        groups = [self.fit_row_ids, self.calibration_fit_ids, self.calibration_audit_ids]
        if any(ids != sorted(set(ids)) for ids in groups) or any(
            set(a) & set(b) for i, a in enumerate(groups) for b in groups[i + 1 :]
        ):
            raise ValueError("Model training/calibration identities overlap or repeat")
        if self.probability_supported and (
            not self.calibration_fit_ids or not self.calibration_audit_ids
        ):
            raise ValueError("Calibrated output requires separate fit and audit cohorts")
        return self

    def predict(self, features: list[FeatureSet]) -> list[ForestPrediction]:
        # Validate copied DTOs as callers may have mutated nested fields after loading.
        model = PortableForest.model_validate(self.model_dump())
        result = []
        for feature in features:
            if digest(feature.lineage.get("config")) != model.feature_spec_sha256:
                raise ValueError("Forest inference feature configuration mismatch")
            values = model_inputs(feature)
            raw = [values[name] for name in MODEL_INPUT_NAMES]
            numeric = [
                (
                    (float(value) if value is not None else model.numeric.medians[i])
                    - model.numeric.means[i]
                )
                / model.numeric.scales[i]
                for i, value in enumerate(raw[: len(FEATURE_NAMES)])
            ]
            vector = np.asarray(
                numeric + [float(bool(v)) for v in raw[len(FEATURE_NAMES) :]], dtype=np.float32
            )
            if not np.all(np.isfinite(vector)):
                raise ValueError("Nonfinite transformed model inputs")
            total = 0.0
            for tree in model.trees:
                index = 0
                while tree.nodes[index].left != -1:
                    node = tree.nodes[index]
                    index = (
                        node.left if float(vector[node.feature]) <= node.threshold else node.right
                    )
                total += tree.nodes[index].class_values[1]
            score = min(1.0, max(0.0, total / len(model.trees)))
            result.append(
                ForestPrediction(
                    snapshot_id=feature.snapshot_id,
                    feature_hash=feature.feature_hash,
                    model_score=score,
                    escalation_probability=model.sigmoid.apply(score) if model.sigmoid else None,
                    score_kind="calibrated_probability" if model.sigmoid else "model_score",
                )
            )
        return result
