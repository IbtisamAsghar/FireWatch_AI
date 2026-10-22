"""Offline forest selection and purged, independently audited sigmoid calibration."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV  # type: ignore[import-untyped]
from sklearn.ensemble import RandomForestClassifier  # type: ignore[import-untyped]
from sklearn.frozen import FrozenEstimator  # type: ignore[import-untyped]
from sklearn.metrics import brier_score_loss, log_loss  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from firewatch.ml.evaluate import ranking_metrics
from firewatch.ml.events import EventCatalog, EventReview, reviewed_groups
from firewatch.ml.forest_artifact import (
    ForestNode,
    ForestTree,
    NumericTransform,
    PortableForest,
    Sigmoid,
)
from firewatch.ml.forest_spec import ForestParameters, ForestSpec
from firewatch.ml.preprocessing import _matrix, development_rows, make_preprocessor
from firewatch.ml.splits import SplitResult, SplitSample, SplitSpec, create_splits
from firewatch.processing.features import MODEL_INPUT_NAMES
from firewatch.sources.firms_normalize import digest


@dataclass(frozen=True)
class ForestData:
    samples: list[SplitSample]
    result: SplitResult
    split_spec: SplitSpec
    catalog: EventCatalog
    review: EventReview | None

    def validate(self) -> None:
        if create_splits(self.samples, self.catalog, self.split_spec, self.review) != self.result:
            raise ValueError(
                "Forest training must match recomputed event-disjoint split membership"
            )


@dataclass(frozen=True)
class ForestRun:
    report: dict[str, Any]
    model: PortableForest | None


def _assert_close(actual: Any, expected: Any, **kwargs: Any) -> None:
    if not np.allclose(actual, expected, **kwargs):
        raise ValueError("Portable model/calibrator differs from fitted estimator")


def _labels(rows: list[SplitSample]) -> Any:
    if not rows or any(r.label.target_value is None for r in rows):
        raise ValueError("Forest fit/evaluation requires observed binary labels")
    return np.asarray([r.label.target_value for r in rows], dtype=int)


def _sufficient(data: ForestData, rows: list[SplitSample], events: int, each_class: int) -> bool:
    ids = {r.row_id for r in rows}
    groups = {m.event_id for m in data.result.membership if m.row_id in ids} - {None}
    labels = [r.label.target_value for r in rows]
    return len(groups) >= events and min(labels.count(0), labels.count(1)) >= each_class


def _fit_pipeline(rows: list[SplitSample], params: ForestParameters, spec: ForestSpec) -> Any:
    model = Pipeline(
        [
            ("preprocess", make_preprocessor()),
            (
                "forest",
                RandomForestClassifier(
                    **params.model_dump(),
                    random_state=spec.random_seed,
                    n_jobs=spec.n_jobs,
                    max_features=spec.max_features,
                    bootstrap=True,
                    criterion="gini",
                ),
            ),
        ]
    )
    model.fit(_matrix(rows), _labels(rows))
    if list(model.named_steps["forest"].classes_) != [0, 1]:
        raise ValueError("Forest estimator must retain the binary target class order")
    return model


def _raw(model: Any, rows: list[SplitSample]) -> list[float]:
    values = model.predict_proba(_matrix(rows))[:, 1]
    if not np.all(np.isfinite(values)) or np.any((values < 0) | (values > 1)):
        raise ValueError("Invalid forest model scores")
    return [float(v) for v in values]


def probability_metrics(rows: list[SplitSample], scores: list[float]) -> dict[str, Any]:
    if (
        len(rows) != len(scores)
        or not scores
        or any(not np.isfinite(v) or not 0 <= v <= 1 for v in scores)
    ):
        raise ValueError("Calibration metrics require bounded, paired scores")
    y = _labels(rows)
    bins = []
    for i in range(5):
        indices = [j for j, value in enumerate(scores) if min(int(value * 5), 4) == i]
        bins.append(
            dict(
                lower=i / 5,
                upper=(i + 1) / 5,
                rows=len(indices),
                mean_score=float(np.mean([scores[j] for j in indices])) if indices else None,
                positive_fraction=float(np.mean(y[indices])) if indices else None,
            )
        )
    return dict(
        brier=float(brier_score_loss(y, scores)),
        log_loss=float(log_loss(y, scores, labels=[0, 1])),
        reliability=bins,
    )


def _cohorts(data: ForestData) -> list[tuple[str | None, list[SplitSample], list[SplitSample]]]:
    folds = (
        [None]
        if data.result.strategy == "chronological"
        else [f.fold_id for f in data.result.folds if f.sufficient]
    )
    cohorts = []
    for fold in folds:
        train = development_rows(data.samples, data.result, role="fit", fold_id=fold)
        validation = development_rows(data.samples, data.result, role="validation", fold_id=fold)
        if not _sufficient(data, train, 3, 5) or not _sufficient(data, validation, 2, 5):
            raise ValueError("Forest selection requires sufficient grouped training and validation")
        cohorts.append((fold, train, validation))
    return cohorts


def _calibration_rows(
    data: ForestData, spec: ForestSpec
) -> tuple[list[SplitSample], list[SplitSample], dict[str, Any]]:
    rows = development_rows(data.samples, data.result, role="calibration")
    block = next(b for b in data.split_spec.blocks if b.partition == "calibration")
    boundary = block.start + (block.end - block.start) * spec.calibration_split_fraction
    purge = timedelta(
        hours=max(spec.calibration_purge_hours, data.split_spec.purge_hours_each_side)
    )
    _, groups = reviewed_groups(data.catalog, data.review)
    meta = {m.row_id: m for m in data.result.membership}
    fit, audit, excluded = [], [], []
    for row in rows:
        member = meta[row.row_id]
        assert member.event_id is not None
        group = groups[member.event_id][0]
        if row.target.feature.as_of < boundary - purge and group.end < boundary - purge:
            fit.append(row)
        elif row.target.feature.as_of > boundary + purge and group.start > boundary + purge:
            audit.append(row)
        else:
            excluded.append(row.row_id)
    return (
        fit,
        audit,
        dict(
            boundary=boundary.isoformat(),
            purge_hours_each_side=purge.total_seconds() / 3600,
            fit_row_ids=sorted(r.row_id for r in fit),
            audit_row_ids=sorted(r.row_id for r in audit),
            excluded_row_ids=sorted(excluded),
            threshold_selection_status="reserved_untouched",
        ),
    )


def _calibrate(
    data: ForestData, spec: ForestSpec, pipeline: Any
) -> tuple[Sigmoid | None, dict[str, Any]]:
    if data.result.strategy != "chronological":
        return None, dict(
            status="cv_uncalibrated",
            reason="No separate chronological calibration/audit blocks under forward CV",
            fit_row_ids=[],
            audit_row_ids=[],
            threshold_selection_status="requires_cross_fitted_threshold_selection",
        )
    fit, audit, info = _calibration_rows(data, spec)
    if not _sufficient(
        data, fit, spec.min_calibration_events, spec.min_calibration_rows_per_class
    ) or not _sufficient(data, audit, spec.min_audit_events, spec.min_audit_rows_per_class):
        return None, info | dict(
            status="insufficient_data",
            reason="Too few independent calibration-fit/audit events or class rows",
        )
    fit_scores = _raw(pipeline, fit)
    if len(set(fit_scores)) < 2:
        return None, info | dict(
            status="insufficient_data", reason="Insufficient calibration score variation"
        )
    calibration = CalibratedClassifierCV(
        FrozenEstimator(pipeline), method="sigmoid", ensemble=False, n_jobs=1
    )
    calibration.fit(_matrix(fit), _labels(fit))
    fitted = calibration.calibrated_classifiers_[0].calibrators[0]
    try:
        sigmoid = Sigmoid(a=float(fitted.a_), b=float(fitted.b_))
    except ValueError:
        return None, info | dict(
            status="rejected", reason="Unstable or decreasing calibration mapping"
        )
    raw = _raw(pipeline, audit)
    adjusted = [sigmoid.apply(value) for value in raw]
    _assert_close(adjusted, calibration.predict_proba(_matrix(audit))[:, 1], rtol=1e-10, atol=1e-12)
    before, after = probability_metrics(audit, raw), probability_metrics(audit, adjusted)
    accepted = (
        after["brier"] <= before["brier"] + 1e-12
        and after["log_loss"] <= before["log_loss"] + 1e-12
    )
    return sigmoid if accepted else None, info | dict(
        status="accepted" if accepted else "rejected",
        reason="Independent development audit gate",
        candidate_sigmoid=sigmoid.model_dump(mode="json"),
        uncalibrated_audit=before,
        calibrated_audit=after,
        audit_row_scores=[
            dict(
                row_id=r.row_id,
                target_value=r.label.target_value,
                model_score=v,
                calibrated_score=p,
            )
            for r, v, p in zip(audit, raw, adjusted, strict=True)
        ],
    )


def _portable(
    data: ForestData,
    spec: ForestSpec,
    pipeline: Any,
    params: ForestParameters,
    train: list[SplitSample],
    fold: str | None,
    sigmoid: Sigmoid | None,
    calibration: dict[str, Any],
) -> PortableForest:
    numeric = pipeline.named_steps["preprocess"].named_transformers_["numeric"]
    forest = pipeline.named_steps["forest"]
    if sum(t.tree_.node_count for t in forest.estimators_) > 100000:
        raise ValueError("Forest exceeds portable node budget")
    trees = []
    for estimator in forest.estimators_:
        tree = estimator.tree_
        nodes = []
        for i in range(tree.node_count):
            values = tree.value[i, 0]
            values = values / np.sum(values)
            nodes.append(
                ForestNode(
                    left=int(tree.children_left[i]),
                    right=int(tree.children_right[i]),
                    feature=int(tree.feature[i]),
                    threshold=float(tree.threshold[i]),
                    class_values=(float(values[0]), float(values[1])),
                    impurity=max(0.0, float(tree.impurity[i])),
                    samples=int(tree.n_node_samples[i]),
                    weighted_samples=float(tree.weighted_n_node_samples[i]),
                )
            )
        trees.append(ForestTree(nodes=nodes))
    return PortableForest(
        feature_names=list(MODEL_INPUT_NAMES),
        feature_spec_sha256=data.split_spec.feature_spec_sha256,
        target_spec_sha256=data.split_spec.target_spec_sha256,
        dataset_sha256=data.result.dataset_sha256,
        split_spec_sha256=data.result.spec_sha256,
        training_spec_sha256=spec.sha256,
        parameters=params,
        fit_row_ids=sorted(r.row_id for r in train),
        fold_id=fold,
        numeric=NumericTransform(
            medians=numeric.named_steps["impute"].statistics_.tolist(),
            means=numeric.named_steps["scale"].mean_.tolist(),
            scales=numeric.named_steps["scale"].scale_.tolist(),
        ),
        trees=trees,
        sigmoid=sigmoid,
        calibration_status=calibration["status"],
        calibration_fit_ids=calibration["fit_row_ids"]
        if calibration["status"] in {"accepted", "rejected"}
        else [],
        calibration_audit_ids=calibration["audit_row_ids"]
        if "uncalibrated_audit" in calibration
        else [],
        probability_supported=sigmoid is not None,
    )


def select_forest_parameters(
    cohorts: list[tuple[str | None, list[SplitSample], list[SplitSample]]], spec: ForestSpec
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not cohorts:
        raise ValueError("Forest selection requires a held-out development cohort")
    candidates: list[dict[str, Any]] = []
    for params in spec.candidates():
        results: list[dict[str, Any]] = []
        for fold, train, validation in cohorts:
            pipeline = _fit_pipeline(train, params, spec)
            scores = _raw(pipeline, validation)
            results.append(
                dict(
                    cohort_id=fold or "chronological-validation",
                    fit_row_ids=sorted(r.row_id for r in train),
                    validation_row_ids=[r.row_id for r in validation],
                    validation_labels=[r.label.target_value for r in validation],
                    validation_scores=scores,
                    ranking=ranking_metrics(validation, scores),
                    raw_score_diagnostics=probability_metrics(validation, scores),
                )
            )
        candidates.append(
            dict(
                parameters=params.model_dump(mode="json"),
                cohorts=results,
                mean_validation_ap=float(
                    np.mean([r["ranking"]["average_precision"] for r in results])
                ),
                mean_validation_brier=float(
                    np.mean([r["raw_score_diagnostics"]["brier"] for r in results])
                ),
            )
        )

    def selection(item: dict[str, Any]) -> tuple[float, float, int, int, int, bool]:
        p = item["parameters"]
        return (
            -item["mean_validation_ap"],
            item["mean_validation_brier"],
            p["n_estimators"],
            p["max_depth"],
            -p["min_samples_leaf"],
            p["class_weight"] is not None,
        )

    chosen = min(candidates, key=selection)
    return chosen, candidates


def train_forest(data: ForestData, spec: ForestSpec | None = None) -> ForestRun:
    spec = ForestSpec.model_validate((spec or ForestSpec()).model_dump())
    data.validate()
    base = dict(
        schema_version="forest-training-report-v1",
        dataset_sha256=data.result.dataset_sha256,
        split_spec_sha256=data.result.spec_sha256,
        catalog_sha256=data.result.catalog_sha256,
        review_sha256=data.result.review_sha256,
        training_spec_sha256=spec.sha256,
        strategy=data.result.strategy,
        final_test_status="sealed",
        final_test_evaluated=False,
        threshold_selection_status=(
            "requires_cross_fitted_threshold_selection"
            if data.result.strategy == "grouped_temporal_cv"
            else "reserved_untouched"
        ),
        promotion_performed=False,
        release_recommendation="baseline_only",
        probability_supported=False,
        evaluation_scope="development_selection_only",
    )
    if data.result.strategy == "baseline_only":
        return ForestRun(
            base
            | dict(
                status="insufficient_data",
                candidate_results=[],
                selected_parameters=None,
                calibration=dict(
                    status="insufficient_data", reason="No eligible development cohorts"
                ),
            ),
            None,
        )
    cohorts = _cohorts(data)
    chosen, candidates = select_forest_parameters(cohorts, spec)
    params = ForestParameters.model_validate(chosen["parameters"])
    fold, train, validation = cohorts[-1]
    pipeline = _fit_pipeline(train, params, spec)
    sigmoid, calibration = _calibrate(data, spec, pipeline)
    model = _portable(data, spec, pipeline, params, train, fold, sigmoid, calibration)
    parity_rows = train + validation
    portable_scores = [
        r.model_score for r in model.predict([row.target.feature for row in parity_rows])
    ]
    np.testing.assert_allclose(portable_scores, _raw(pipeline, parity_rows), rtol=1e-10, atol=1e-12)
    class_effects = {
        str(weight): [
            dict(
                parameters=c["parameters"],
                mean_validation_ap=c["mean_validation_ap"],
                mean_validation_brier=c["mean_validation_brier"],
            )
            for c in candidates
            if c["parameters"]["class_weight"] == weight
        ]
        for weight in (None, "balanced")
    }
    return ForestRun(
        base
        | dict(
            status="fitted_development_only",
            candidate_results=candidates,
            selected_parameters=params.model_dump(mode="json"),
            selected_fold_id=fold,
            estimator_parameters=pipeline.named_steps["forest"].get_params(deep=False),
            class_weight_comparison=class_effects,
            calibration=calibration,
            probability_supported=model.probability_supported,
            model_sha256=digest(model.model_dump(mode="json")),
            portability_parity_rows=len(parity_rows),
        ),
        model,
    )
