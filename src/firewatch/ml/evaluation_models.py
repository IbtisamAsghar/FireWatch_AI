"""Frozen paired scorers, honest weather/climate ablations and nested temporal selection."""

from typing import Any

import numpy as np
from pydantic import Field
from sklearn.compose import ColumnTransformer  # type: ignore[import-untyped]
from sklearn.ensemble import RandomForestClassifier  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from firewatch.ml.baselines import WeightedPolicy, fit_weighted_policy, weighted_index
from firewatch.ml.forest_artifact import NumericTransform, PortableForest
from firewatch.ml.forest_spec import ForestParameters, ForestSpec
from firewatch.ml.forest_training import (
    ForestData,
    _fit_pipeline,
    _labels,
    _portable,
    select_forest_parameters,
)
from firewatch.ml.preprocessing import _matrix, development_rows, make_preprocessor
from firewatch.ml.splits import SplitSample
from firewatch.ml.train import fit_logistic, logistic_metadata
from firewatch.processing.features import (
    FEATURE_NAMES,
    MODEL_INPUT_NAMES,
    FeatureValues,
    model_inputs,
)
from firewatch.schemas.common import DTO


class LinearScorer(DTO):
    coefficients: list[float] = Field(min_length=31, max_length=31)
    intercept: float
    numeric: NumericTransform

    def scores(self, rows: list[SplitSample]) -> list[float]:
        result = []
        for row in rows:
            values = model_inputs(row.target.feature)
            raw = [values[name] for name in MODEL_INPUT_NAMES]
            numeric = [
                ((float(v) if v is not None else self.numeric.medians[i]) - self.numeric.means[i])
                / self.numeric.scales[i]
                for i, v in enumerate(raw[:17])
            ]
            value = float(
                np.dot(numeric + [float(bool(v)) for v in raw[17:]], self.coefficients)
                + self.intercept
            )
            if not np.isfinite(value):
                raise ValueError("Nonfinite logistic evaluation score")
            result.append(value)
        return result


class EvaluationModels(DTO):
    weighted: WeightedPolicy
    forest: PortableForest
    logistic: LinearScorer
    logistic_fit_row_ids: list[str]
    ablations: dict[str, PortableForest]
    ablation_features: dict[str, list[str]]
    selection_evidence: dict[str, Any]

    def predictions(
        self, rows: list[SplitSample]
    ) -> tuple[dict[str, list[float]], dict[str, list[float]]]:
        main = self.forest.predict([row.target.feature for row in rows])
        scores = {
            "baseline-v1": [
                float(FeatureValues.model_validate(row.target.feature.values).frp_max_mw or 0)
                for row in rows
            ],
            "weighted-baseline-v1": [
                weighted_index(row.target.feature, self.weighted) for row in rows
            ],
            "logistic": self.logistic.scores(rows),
            "forest": [
                float(p.escalation_probability)
                if p.escalation_probability is not None
                else p.model_score
                for p in main
            ],
        }
        probabilities = (
            {
                "forest": [
                    float(p.escalation_probability)
                    for p in main
                    if p.escalation_probability is not None
                ]
            }
            if self.forest.probability_supported
            else {}
        )
        scores.update(
            {
                name: [p.model_score for p in model.predict([r.target.feature for r in rows])]
                for name, model in self.ablations.items()
            }
        )
        return scores, probabilities


def _ablation(
    data: ForestData,
    config: ForestSpec,
    model: PortableForest,
    train: list[SplitSample],
    names: list[str],
) -> PortableForest:
    indices = [MODEL_INPUT_NAMES.index(name) for name in names]
    pipeline = Pipeline(
        [
            ("preprocess", make_preprocessor()),
            ("select", ColumnTransformer([("selected", "passthrough", indices)], remainder="drop")),
            (
                "forest",
                RandomForestClassifier(
                    **model.parameters.model_dump(),
                    random_state=42,
                    n_jobs=1,
                    max_features="sqrt",
                    bootstrap=True,
                    criterion="gini",
                ),
            ),
        ]
    )
    pipeline.fit(_matrix(train), _labels(train))
    artifact = _portable(
        data,
        config,
        pipeline,
        model.parameters,
        train,
        model.fold_id,
        None,
        dict(status="insufficient_data", fit_row_ids=[], audit_row_ids=[]),
    )
    for tree in artifact.trees:
        for node in tree.nodes:
            if node.feature >= 0:
                node.feature = indices[node.feature]
    artifact = PortableForest.model_validate(artifact.model_dump())
    actual = [p.model_score for p in artifact.predict([r.target.feature for r in train])]
    if not np.allclose(
        actual, pipeline.predict_proba(_matrix(train))[:, 1], rtol=1e-10, atol=1e-12
    ):
        raise ValueError("Portable ablation differs from fitted feature-subset model")
    return artifact


def fit_evaluation_models(
    data: ForestData, model: PortableForest, config: ForestSpec
) -> EvaluationModels:
    evidence: dict[str, Any] = dict(mode="chronological_reserved_threshold_block")
    fold_id = model.fold_id
    if data.result.strategy == "grouped_temporal_cv":
        folds = [f for f in data.result.folds if f.sufficient]
        outer = folds[-1]
        inner = [f for f in folds[:-1] if f.end <= outer.boundary]
        if len(inner) < 2:
            raise ValueError(
                "Nested threshold selection requires two earlier sufficient forward folds"
            )
        outer_train = development_rows(data.samples, data.result, role="fit", fold_id=outer.fold_id)
        outer_ids = {r.row_id for r in outer_train}
        cohorts: list[tuple[str | None, list[SplitSample], list[SplitSample]]] = []
        for f in inner:
            train = development_rows(data.samples, data.result, role="fit", fold_id=f.fold_id)
            validation = development_rows(
                data.samples, data.result, role="validation", fold_id=f.fold_id
            )
            if not {r.row_id for r in train + validation} <= outer_ids:
                raise ValueError("Nested selection escapes the outer training period")
            cohorts.append((f.fold_id, train, validation))
        chosen, candidates = select_forest_parameters(cohorts, config)
        params = ForestParameters.model_validate(chosen["parameters"])
        pipeline = _fit_pipeline(outer_train, params, config)
        model = _portable(
            data,
            config,
            pipeline,
            params,
            outer_train,
            outer.fold_id,
            None,
            dict(status="cv_uncalibrated", fit_row_ids=[], audit_row_ids=[]),
        )
        fold_id = outer.fold_id
        evidence = dict(
            mode="nested_forward_selection",
            outer_fold_id=fold_id,
            inner_fold_ids=[f.fold_id for f in inner],
            candidate_results=candidates,
            selected_parameters=params.model_dump(mode="json"),
            source_candidate_replaced=True,
        )
    train = development_rows(data.samples, data.result, role="fit", fold_id=fold_id)
    fitted = fit_logistic(data.samples, data.result, fold_id=fold_id)
    metadata = logistic_metadata(fitted)
    linear = LinearScorer(
        coefficients=metadata["coefficients"][0],
        intercept=metadata["intercept"][0],
        numeric=NumericTransform(
            medians=metadata["imputer_statistics"],
            means=metadata["scaler_mean"],
            scales=metadata["scaler_scale"],
        ),
    )
    if not np.allclose(
        linear.scores(train),
        fitted.pipeline.decision_function(_matrix(train)),
        rtol=1e-10,
        atol=1e-12,
    ):
        raise ValueError("Portable logistic evaluator differs from fitted pipeline")
    climate = {"temperature_departure_c", "rainfall_deficit_24h_mm"}
    satellite = set(FEATURE_NAMES[:9])
    masks = {
        "forest_no_weather_climate": [
            name for name in MODEL_INPUT_NAMES if name.removesuffix("_missing") in satellite
        ],
        "forest_no_climate": [
            name for name in MODEL_INPUT_NAMES if name.removesuffix("_missing") not in climate
        ],
    }
    return EvaluationModels(
        weighted=fit_weighted_policy(data.samples, data.result, fold_id=fold_id),
        forest=model,
        logistic=linear,
        logistic_fit_row_ids=list(fitted.fit_row_ids),
        ablations={
            name: _ablation(data, config, model, train, names) for name, names in masks.items()
        },
        ablation_features=masks,
        selection_evidence=evidence,
    )
