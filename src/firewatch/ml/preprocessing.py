"""Training-only preprocessing access for verified split memberships; no estimator training."""

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.compose import ColumnTransformer  # type: ignore[import-untyped]
from sklearn.impute import SimpleImputer  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from firewatch.ml.splits import SplitResult, SplitSample, sample_checksum
from firewatch.processing.features import FEATURE_NAMES, MODEL_INPUT_NAMES, model_inputs


@dataclass(frozen=True)
class FittedPreprocessor:
    transformer: Any
    fit_row_ids: tuple[str, ...]
    dataset_sha256: str
    feature_names: tuple[str, ...] = MODEL_INPUT_NAMES


def _lookup(samples: list[SplitSample], result: SplitResult) -> dict[str, SplitSample]:
    if sample_checksum(samples) != result.dataset_sha256:
        raise ValueError("Preprocessing input dataset changed after splitting")
    lookup = {s.row_id: s for s in samples}
    if set(lookup) != {m.row_id for m in result.membership}:
        raise ValueError("Preprocessing membership does not cover exact dataset")
    return lookup


def development_rows(
    samples: list[SplitSample], result: SplitResult, *, role: str, fold_id: str | None = None
) -> list[SplitSample]:
    lookup = _lookup(samples, result)
    if role not in {"fit", "validation", "calibration", "threshold"}:
        raise ValueError("Final test is sealed; unsupported development role")
    if result.strategy == "baseline_only":
        raise ValueError("Insufficient split data; retain baseline-only MVP")
    if fold_id:
        fold = next((f for f in result.folds if f.fold_id == fold_id and f.sufficient), None)
        if fold is None or role not in {"fit", "validation"}:
            raise ValueError("Use a sufficient development fold and fit/validation role")
        ids = fold.train_ids if role == "fit" else fold.validation_ids
        if any(lookup[key].target.feature.as_of >= fold.end for key in ids):
            raise ValueError("Development fold includes future/holdout rows")
    else:
        if result.strategy != "chronological":
            raise ValueError("Grouped CV preprocessing requires an explicit sufficient fold")
        partition = "train" if role == "fit" else role
        ids = [m.row_id for m in result.membership if m.partition == partition]
    meta = {m.row_id: m for m in result.membership}
    if any(meta[key].partition == "final_test" for key in ids):
        raise ValueError("Final test cannot enter development preprocessing")
    return [lookup[key] for key in sorted(ids)]


def _matrix(rows: list[SplitSample]) -> Any:
    if not rows:
        raise ValueError("No permitted rows for preprocessing")
    return np.asarray(
        [[model_inputs(row.target.feature)[name] for name in MODEL_INPUT_NAMES] for row in rows],
        dtype=np.float64,
    )


def fit_preprocessor(
    samples: list[SplitSample], result: SplitResult, *, fold_id: str | None = None
) -> FittedPreprocessor:
    rows = development_rows(samples, result, role="fit", fold_id=fold_id)
    # Missing flags already exist in the frozen whitelist. All-missing measurements
    # use the imputer's documented zero floor while their missing flags remain true.
    numeric = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scale", StandardScaler()),
        ]
    )
    transformer = ColumnTransformer(
        [
            ("numeric", numeric, list(range(len(FEATURE_NAMES)))),
            (
                "missing_flags",
                "passthrough",
                list(range(len(FEATURE_NAMES), len(MODEL_INPUT_NAMES))),
            ),
        ]
    )
    transformer.fit(_matrix(rows))
    return FittedPreprocessor(transformer, tuple(row.row_id for row in rows), result.dataset_sha256)


def transform_development(
    fitted: FittedPreprocessor,
    samples: list[SplitSample],
    result: SplitResult,
    *,
    role: str,
    fold_id: str | None = None,
) -> Any:
    if fitted.dataset_sha256 != result.dataset_sha256:
        raise ValueError("Fitted preprocessing belongs to a different dataset")
    expected = development_rows(samples, result, role="fit", fold_id=fold_id)
    if fitted.fit_row_ids != tuple(row.row_id for row in expected):
        raise ValueError("Preprocessing was not fitted on this training partition/fold")
    return fitted.transformer.transform(
        _matrix(development_rows(samples, result, role=role, fold_id=fold_id))
    )
