"""Paired metrics, explicit strata and event-cluster bootstrap uncertainty."""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.metrics import auc, average_precision_score, precision_recall_curve  # type: ignore[import-untyped]

from firewatch.ml.evaluation_spec import EvaluationSpec
from firewatch.ml.forest_training import ForestData
from firewatch.ml.priority_thresholds import PriorityThresholds
from firewatch.ml.splits import SplitSample

WEATHER_FIELDS = (
    "temperature_c",
    "humidity_pct",
    "wind_ms",
    "gust_ms",
    "precipitation_24h_mm",
    "weather_age_hours",
)


@dataclass(frozen=True)
class EvaluationRow:
    row_id: str
    event_id: str
    run_id: str
    month: str
    sensor_product: str
    weather_completeness: str
    unobserved_followup: str
    label: int


def evaluation_rows(data: ForestData, rows: list[SplitSample]) -> list[EvaluationRow]:
    meta = {m.row_id: m for m in data.result.membership}
    result = []
    for sample in rows:
        row = meta[sample.row_id]
        if (
            row.event_id is None
            or row.target_value is None
            or (
                set(row.exclusion_reasons)
                - (
                    {"boundary_purge", "cross_boundary_event", "event_period_partition_mismatch"}
                    if data.result.strategy == "grouped_temporal_cv"
                    else set()
                )
            )
        ):
            raise ValueError("Evaluation requires eligible source-bound event/label membership")
        flags = sample.target.feature.missing_flags
        missing = sum(flags[name] for name in WEATHER_FIELDS)
        result.append(
            EvaluationRow(
                row.row_id,
                row.event_id,
                str(sample.target.feature.run_id),
                sample.target.feature.as_of.strftime("%Y-%m"),
                ";".join(sorted(f"{s}:{p}" for s, p in sample.target.products.items())),
                "complete"
                if missing == 0
                else "missing"
                if missing == len(WEATHER_FIELDS)
                else "partial",
                str(sample.label.unobserved_followup).lower(),
                int(row.target_value),
            )
        )
    return result


def metrics(
    rows: list[EvaluationRow],
    scores: list[float],
    threshold: float,
    probabilities: list[float] | None = None,
) -> dict[str, Any]:
    if (
        not rows
        or len(rows) != len(scores)
        or not all(math.isfinite(s) for s in scores)
        or not math.isfinite(threshold)
    ):
        raise ValueError("Metrics require finite, paired nonempty scores")
    if any(row.label not in (0, 1) for row in rows):
        raise ValueError("Evaluation requires binary observed labels")
    y = [r.label for r in rows]
    positives = sum(y)
    predicted = [s >= threshold for s in scores]
    tp = sum(bool(label) and p for label, p in zip(y, predicted, strict=True))
    fp = sum(not label and p for label, p in zip(y, predicted, strict=True))
    fn = positives - tp
    tn = len(rows) - positives - fp
    runs: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        runs[row.run_id].append(i)
    precisions, recalls = [], []
    for indices in runs.values():
        ordered = sorted(indices, key=lambda i: (-scores[i], rows[i].row_id))
        k = min(10, len(ordered))
        found, count = sum(y[i] for i in ordered[:k]), sum(y[i] for i in ordered)
        precisions.append(found / k)
        if count:
            recalls.append(found / count)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / positives if positives else None
    pr_auc = ap = None
    if positives:
        curve_precision, curve_recall, _ = precision_recall_curve(y, scores)
        pr_auc = float(auc(curve_recall, curve_precision))
        ap = float(average_precision_score(y, scores))
    probability = None
    if probabilities is not None:
        if len(probabilities) != len(rows) or any(
            not math.isfinite(v) or not 0 <= v <= 1 for v in probabilities
        ):
            raise ValueError("Claimed probabilities must be paired and bounded")
        bins = []
        for i in range(5):
            selected = [j for j, v in enumerate(probabilities) if min(int(v * 5), 4) == i]
            bins.append(
                dict(
                    lower=i / 5,
                    upper=(i + 1) / 5,
                    rows=len(selected),
                    mean_probability=float(np.mean([probabilities[j] for j in selected]))
                    if selected
                    else None,
                    observed_fraction=float(np.mean([y[j] for j in selected]))
                    if selected
                    else None,
                )
            )
        probability = dict(
            brier=float(
                np.mean([(v - label) ** 2 for v, label in zip(probabilities, y, strict=True)])
            ),
            reliability=bins,
        )
    return dict(
        rows=len(rows),
        events=len({r.event_id for r in rows}),
        positive=positives,
        negative=len(rows) - positives,
        prevalence=positives / len(rows),
        precision=precision,
        recall=recall,
        f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
        confusion_matrix=dict(labels=[0, 1], values=[[tn, fp], [fn, tp]]),
        average_precision=ap,
        pr_auc=pr_auc,
        pr_auc_definition="trapezoidal_precision_recall_curve",
        precision_at_10=float(np.mean(precisions)),
        recall_at_10=float(np.mean(recalls)) if recalls else None,
        ranking_runs=len(runs),
        recall_runs=len(recalls),
        runs_without_positives=len(runs) - len(recalls),
        undefined_recall=not bool(positives),
        probability=probability,
    )


def breakdowns(
    rows: list[EvaluationRow],
    scores: list[float],
    threshold: float,
    probabilities: list[float] | None = None,
) -> dict[str, Any]:
    result = {}
    for key in (
        "event_id",
        "month",
        "sensor_product",
        "weather_completeness",
        "unobserved_followup",
    ):
        groups: dict[str, list[int]] = defaultdict(list)
        for i, row in enumerate(rows):
            groups[str(getattr(row, key))].append(i)
        result[key] = {
            name: metrics(
                [rows[i] for i in ids],
                [scores[i] for i in ids],
                threshold,
                [probabilities[i] for i in ids] if probabilities is not None else None,
            )
            for name, ids in sorted(groups.items())
        }
    return result


def event_bootstrap(
    rows: list[EvaluationRow],
    methods: dict[str, list[float]],
    thresholds: dict[str, PriorityThresholds],
    spec: EvaluationSpec,
) -> dict[str, Any]:
    if not methods or set(methods) != set(thresholds):
        raise ValueError("Bootstrap requires matching methods and thresholds")
    for name, scores in methods.items():
        metrics(rows, scores, thresholds[name].high)
    groups: dict[str, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        groups[row.event_id].append(i)
    if len(groups) < spec.min_bootstrap_events:
        return dict(
            status="insufficient_events", events=len(groups), intervals={}, paired_deltas={}
        )
    names = sorted(groups)
    rng = np.random.default_rng(spec.bootstrap_seed)
    collected: dict[str, dict[str, list[float]]] = {name: defaultdict(list) for name in methods}
    paired: dict[str, list[float]] = defaultdict(list)
    for _ in range(spec.bootstrap_repetitions):
        chosen = rng.integers(0, len(names), size=len(names))
        ids = [i for j in chosen for i in groups[names[int(j)]]]
        sampled = [rows[i] for i in ids]
        values = {}
        for name, scores in methods.items():
            value = metrics(sampled, [scores[i] for i in ids], thresholds[name].high)
            values[name] = value
            for metric in (
                "precision",
                "recall",
                "f1",
                "average_precision",
                "pr_auc",
                "precision_at_10",
                "recall_at_10",
            ):
                if value[metric] is not None:
                    collected[name][metric].append(float(value[metric]))
        if "forest" in values:
            for other in methods:
                if (
                    other != "forest"
                    and values["forest"]["average_precision"] is not None
                    and values[other]["average_precision"] is not None
                ):
                    paired["forest_minus_" + other].append(
                        values["forest"]["average_precision"] - values[other]["average_precision"]
                    )

    def interval(values: list[float]) -> dict[str, Any]:
        return (
            dict(
                lower=float(np.quantile(values, 0.025)),
                upper=float(np.quantile(values, 0.975)),
                valid_replicates=len(values),
            )
            if values
            else dict(lower=None, upper=None, valid_replicates=0)
        )

    return dict(
        status="estimated",
        events=len(groups),
        repetitions=spec.bootstrap_repetitions,
        resampling_unit="whole_partition_event",
        confidence_level=95,
        intervals={
            name: {metric: interval(v) for metric, v in values.items()}
            for name, values in collected.items()
        },
        paired_deltas={name: interval(values) for name, values in paired.items()},
    )
