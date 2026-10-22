"""Offline fixed logistic comparison; no artifact loading or runtime promotion."""

import warnings
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]

from firewatch.ml.preprocessing import _matrix, development_rows, make_preprocessor
from firewatch.ml.splits import SplitResult, SplitSample
from firewatch.processing.features import MODEL_INPUT_NAMES


@dataclass(frozen=True)
class LogisticComparison:
    pipeline: Any
    dataset_sha256: str
    fit_row_ids: tuple[str, ...]
    fold_id: str | None


def fit_logistic(
    samples: list[SplitSample], result: SplitResult, *, fold_id: str | None = None
) -> LogisticComparison:
    rows = development_rows(samples, result, role="fit", fold_id=fold_id)
    labels = [row.label.target_value for row in rows]
    fit_ids = {row.row_id for row in rows}
    events = {m.event_id for m in result.membership if m.row_id in fit_ids}
    if min(labels.count(0), labels.count(1)) < 5 or len(events - {None}) < 3:
        raise ValueError(
            "Logistic comparison requires three training events and five rows per class"
        )
    model = Pipeline(
        [
            ("preprocess", make_preprocessor()),
            (
                "logistic",
                LogisticRegression(
                    C=1.0,
                    solver="lbfgs",
                    max_iter=1000,
                    tol=1e-6,
                    random_state=42,
                    class_weight=None,
                ),
            ),
        ]
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        try:
            model.fit(_matrix(rows), np.asarray(labels, dtype=int))
        except ConvergenceWarning:
            raise ValueError("Logistic comparison did not converge; no results published") from None
    return LogisticComparison(
        model, result.dataset_sha256, tuple(row.row_id for row in rows), fold_id
    )


def logistic_scores(
    fitted: LogisticComparison,
    samples: list[SplitSample],
    result: SplitResult,
    *,
    role: str = "validation",
) -> tuple[list[SplitSample], list[float]]:
    training = development_rows(samples, result, role="fit", fold_id=fitted.fold_id)
    if fitted.dataset_sha256 != result.dataset_sha256 or fitted.fit_row_ids != tuple(
        row.row_id for row in training
    ):
        raise ValueError("Logistic fit does not match this dataset and training cohort")
    rows = development_rows(samples, result, role=role, fold_id=fitted.fold_id)
    scores = fitted.pipeline.decision_function(_matrix(rows))
    if not np.all(np.isfinite(scores)):
        raise ValueError("Nonfinite logistic comparison scores")
    return rows, [float(value) for value in scores]


def logistic_metadata(fitted: LogisticComparison) -> dict[str, Any]:
    model = fitted.pipeline.named_steps["logistic"]
    numeric = fitted.pipeline.named_steps["preprocess"].named_transformers_["numeric"]
    return dict(
        method="logistic-regression-comparison-v1",
        score_kind="model_score",
        probability_supported=False,
        feature_names=list(MODEL_INPUT_NAMES),
        dataset_sha256=fitted.dataset_sha256,
        fit_row_ids=list(fitted.fit_row_ids),
        fold_id=fitted.fold_id,
        parameters=dict(
            C=1.0, solver="lbfgs", max_iter=1000, tol=1e-6, random_state=42, class_weight=None
        ),
        coefficients=model.coef_.tolist(),
        intercept=model.intercept_.tolist(),
        imputer_statistics=numeric.named_steps["impute"].statistics_.tolist(),
        scaler_mean=numeric.named_steps["scale"].mean_.tolist(),
        scaler_scale=numeric.named_steps["scale"].scale_.tolist(),
        iterations=model.n_iter_.tolist(),
        calibration_status="not_calibrated",
    )
