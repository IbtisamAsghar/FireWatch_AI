"""Paired development-holdout ranking comparisons; final release test stays sealed."""

from collections import defaultdict
from typing import Any

from sklearn.metrics import average_precision_score, roc_auc_score  # type: ignore[import-untyped]

from firewatch.ml.baselines import WeightedPolicy, fit_weighted_policy, weighted_index
from firewatch.ml.preprocessing import development_rows
from firewatch.ml.splits import SplitResult, SplitSample, sample_checksum
from firewatch.ml.train import fit_logistic, logistic_metadata, logistic_scores
from firewatch.processing.features import FeatureValues
from firewatch.sources.firms_normalize import digest


def ranking_metrics(rows: list[SplitSample], scores: list[float]) -> dict[str, Any]:
    if not rows or len(rows) != len(scores):
        raise ValueError("Metrics require a nonempty identical prediction/label cohort")
    if any(row.label.target_value is None or row.label.label == "unknown" for row in rows):
        raise ValueError("Unknown labels cannot enter ranking evaluation")
    labels = [int(row.label.target_value or 0) for row in rows]
    counts = sum(labels)
    by_run: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        by_run[str(row.target.feature.run_id)].append(i)
    precisions, recalls = [], []
    for indices in by_run.values():
        ordered = sorted(indices, key=lambda i: (-scores[i], rows[i].row_id))
        k = min(10, len(ordered))
        found = sum(labels[i] for i in ordered[:k])
        positives = sum(labels[i] for i in ordered)
        precisions.append(found / k)
        if positives:
            recalls.append(found / positives)
    return dict(
        rows=len(rows),
        positive=counts,
        negative=len(rows) - counts,
        prevalence=counts / len(rows),
        average_precision=float(average_precision_score(labels, scores)) if counts else None,
        roc_auc=float(roc_auc_score(labels, scores)) if 0 < counts < len(rows) else None,
        mean_run_precision_at_10=sum(precisions) / len(precisions),
        mean_run_recall_at_10=sum(recalls) / len(recalls) if recalls else None,
        runs=len(by_run),
        recall_runs=len(recalls),
        runs_without_positives=len(by_run) - len(recalls),
        tie_policy="descending_score_then_row_id",
        uncertainty_estimated=False,
    )


def _cohort(samples: list[SplitSample], result: SplitResult, fold_id: str | None) -> dict[str, Any]:
    rows = development_rows(samples, result, role="validation", fold_id=fold_id)
    if not rows or len({r.label.target_value for r in rows}) != 2:
        raise ValueError("Comparison validation cohort requires both classes")
    policy = fit_weighted_policy(samples, result, fold_id=fold_id)
    logistic = fit_logistic(samples, result, fold_id=fold_id)
    logit_rows, logit_scores = logistic_scores(logistic, samples, result)
    if [r.row_id for r in logit_rows] != [r.row_id for r in rows]:
        raise ValueError("Methods must use identical held-out rows")
    predictions = {
        "baseline-v1": [
            float(FeatureValues.model_validate(r.target.feature.values).frp_max_mw or 0)
            for r in rows
        ],
        "weighted-baseline-v1": [weighted_index(r.target.feature, policy) for r in rows],
        "logistic-comparison-v1": logit_scores,
    }
    validation_ids = {row.row_id for row in rows}
    return dict(
        cohort_id=fold_id or "chronological-validation",
        role="validation",
        row_ids=[r.row_id for r in rows],
        labels=[r.label.target_value for r in rows],
        event_ids=sorted(
            {
                m.event_id
                for m in result.membership
                if m.row_id in validation_ids and m.event_id is not None
            }
        ),
        cohort_sha256=digest([dict(row_id=r.row_id, label=r.label.target_value) for r in rows]),
        weighted_policy=policy.model_dump(mode="json"),
        weighted_policy_sha256=policy.sha256,
        logistic=logistic_metadata(logistic),
        methods={
            name: dict(
                score_kind="model_score" if name.startswith("logistic") else "priority_score",
                escalation_probability=None,
                scores=values,
                metrics=ranking_metrics(rows, values),
            )
            for name, values in predictions.items()
        },
    )


def compare_baselines(samples: list[SplitSample], result: SplitResult) -> dict[str, Any]:
    if sample_checksum(samples) != result.dataset_sha256:
        raise ValueError("Comparison inputs changed after partitioning")
    cohorts = []
    if result.strategy == "chronological":
        cohorts.append(_cohort(samples, result, None))
    elif result.strategy == "grouped_temporal_cv":
        cohorts.extend(_cohort(samples, result, f.fold_id) for f in result.folds if f.sufficient)
    return dict(
        schema_version="baseline-comparison-v1",
        dataset_sha256=result.dataset_sha256,
        split_spec_sha256=result.spec_sha256,
        catalog_sha256=result.catalog_sha256,
        review_sha256=result.review_sha256,
        strategy=result.strategy,
        status="development_comparison" if cohorts else "insufficient_data",
        logistic_status="fitted_development_only" if cohorts else "skipped_insufficient_data",
        cohort_count=len(cohorts),
        cohorts=cohorts,
        prototype_weighted_policy=WeightedPolicy().model_dump(mode="json"),
        weights_status="prespecified_prototype_not_optimized",
        final_test_status="sealed",
        final_test_evaluated=False,
        promotion_performed=False,
        probability_supported=False,
        release_recommendation="baseline_only",
        limitations=[
            "Development comparison does not establish final release performance",
            "Fixed index weights and display thresholds remain provisional",
            "Observed-FRP target and thermal event groups are satellite proxies",
        ],
    )
