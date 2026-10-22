"""Development-freeze and release reports. Call release only through the durable ledger."""

from typing import Any

from firewatch.ml.evaluation_models import EvaluationModels, fit_evaluation_models
from firewatch.ml.evaluation_spec import EvaluationSpec
from firewatch.ml.forest_artifact import PortableForest
from firewatch.ml.forest_spec import ForestSpec
from firewatch.ml.forest_training import ForestData
from firewatch.ml.preprocessing import development_rows
from firewatch.ml.priority_thresholds import (
    PriorityThresholds,
    baseline_thresholds,
    choose_thresholds,
)
from firewatch.ml.release_metrics import breakdowns, evaluation_rows, event_bootstrap, metrics
from firewatch.ml.splits import SplitSample
from firewatch.sources.firms_normalize import digest


def holdout_key(data: ForestData) -> str:
    block = data.split_spec.blocks[-1]
    # Stable across model/config/source revisions: a new model cannot buy another look.
    return digest(
        dict(
            region="northern-california",
            bbox=[-122.5, 38.5, -120.0, 40.5],
            target=data.split_spec.target_spec_sha256,
            start=block.start.isoformat(),
            end=block.end.isoformat(),
        )
    )


def _enough(data: ForestData, rows: list[SplitSample], events: int, each: int) -> bool:
    ids = {r.row_id for r in rows}
    groups = {m.event_id for m in data.result.membership if m.row_id in ids} - {None}
    labels = [r.label.target_value for r in rows]
    return len(groups) >= events and min(labels.count(0), labels.count(1)) >= each


def _cohort_report(
    data: ForestData,
    rows: list[SplitSample],
    models: EvaluationModels,
    thresholds: dict[str, PriorityThresholds],
    spec: EvaluationSpec,
) -> dict[str, Any]:
    frame = evaluation_rows(data, rows)
    scores, probabilities = models.predictions(rows)
    return dict(
        row_ids=[r.row_id for r in frame],
        labels=[r.label for r in frame],
        event_ids=[r.event_id for r in frame],
        methods={
            name: dict(
                scores=values,
                priorities=[thresholds[name].priority(v) for v in values],
                score_kind="calibrated_probability"
                if name in probabilities
                else "priority_score"
                if name.endswith("baseline-v1") or name == "baseline-v1"
                else "model_score",
                escalation_probability=probabilities.get(name),
                metrics=metrics(frame, values, thresholds[name].high, probabilities.get(name)),
                breakdowns=breakdowns(
                    frame, values, thresholds[name].high, probabilities.get(name)
                ),
            )
            for name, values in scores.items()
        },
        uncertainty=event_bootstrap(frame, scores, thresholds, spec),
    )


def prepare_report(
    data: ForestData, model: PortableForest | None, training_spec: ForestSpec, spec: EvaluationSpec
) -> tuple[dict[str, Any], EvaluationModels | None]:
    data.validate()
    base = dict(
        schema_version="evaluation-development-v1",
        dataset_sha256=data.result.dataset_sha256,
        split_spec_sha256=data.result.spec_sha256,
        evaluation_spec_sha256=spec.sha256,
        holdout_key=holdout_key(data),
        final_test_status="sealed",
        final_test_evaluated=False,
        promotion_performed=False,
        release_recommendation="baseline_only",
        prototype_thresholds={
            name: baseline_thresholds(name).model_dump(mode="json")
            for name in ("baseline-v1", "weighted-baseline-v1")
        },
    )
    if model is None or data.result.strategy == "baseline_only":
        return base | dict(
            status="insufficient_data",
            thresholds={},
            development=None,
            failures=["No eligible fitted forest/development cohort"],
            ablation_status="not_evaluated",
        ), None
    if (
        model.dataset_sha256 != data.result.dataset_sha256
        or model.split_spec_sha256 != data.result.spec_sha256
    ):
        raise ValueError("Evaluation model is not bound to this cohort")
    if (
        data.result.strategy == "grouped_temporal_cv"
        and sum(f.sufficient for f in data.result.folds) < 3
    ):
        return base | dict(
            status="insufficient_nested_development_data",
            thresholds={},
            development=None,
            failures=[
                "Need two earlier sufficient inner folds and a separate outer threshold fold"
            ],
            ablation_status="not_evaluated",
        ), None
    rows = (
        development_rows(data.samples, data.result, role="threshold")
        if data.result.strategy == "chronological"
        else development_rows(
            data.samples,
            data.result,
            role="validation",
            fold_id=[f.fold_id for f in data.result.folds if f.sufficient][-1],
        )
    )
    if not _enough(data, rows, spec.min_threshold_events, spec.min_threshold_rows_per_class):
        return base | dict(
            status="insufficient_threshold_data",
            thresholds={},
            development=None,
            failures=["Threshold cohort lacks independent events or both classes"],
            ablation_status="not_evaluated",
        ), None
    models = fit_evaluation_models(data, model, training_spec)
    if {r.row_id for r in rows} & (
        set(models.forest.fit_row_ids)
        | set(models.forest.calibration_fit_ids)
        | set(models.forest.calibration_audit_ids)
    ):
        raise ValueError("Threshold selection overlaps fitting/calibration")
    scores, _ = models.predictions(rows)
    units = {
        "baseline-v1": "MW",
        "weighted-baseline-v1": "index_0_100",
        "logistic": "decision_margin",
        "forest": "calibrated_probability"
        if models.forest.probability_supported
        else "forest_vote_fraction",
    }
    thresholds = {
        name: choose_thresholds(
            name,
            units.get(name, "forest_vote_fraction"),
            [r.row_id for r in rows],
            [int(r.label.target_value or 0) for r in rows],
            values,
        )
        for name, values in scores.items()
    }
    development = _cohort_report(data, rows, models, thresholds, spec)
    failures = [
        "Threshold-cohort metrics were used for selection; they are not release performance"
    ]
    if any(t.degenerate_bands for t in thresholds.values()):
        failures.append("Some score scales cannot distinguish all four priority bands")
    return base | dict(
        status="development_frozen",
        thresholds={k: v.model_dump(mode="json") for k, v in thresholds.items()},
        development=development,
        failures=failures,
        ablation_status="development_only",
        selection_evidence=models.selection_evidence,
        model_sha256=digest(models.model_dump(mode="json")),
    ), models


def final_eligible(data: ForestData, spec: EvaluationSpec) -> bool:
    count = data.result.partition_counts["final_test"]
    return (
        count["events"] >= spec.min_final_events
        and min(count["positive"], count["negative"]) >= spec.min_final_rows_per_class
    )


def release_report(
    data: ForestData, development: dict[str, Any], models: EvaluationModels, spec: EvaluationSpec
) -> dict[str, Any]:
    # Internal pure function. The export/CLI reserves the one-shot ledger before calling.
    if (
        development["status"] != "development_frozen"
        or development["evaluation_spec_sha256"] != spec.sha256
        or development["dataset_sha256"] != data.result.dataset_sha256
    ):
        raise ValueError("Final evaluation requires a matching frozen development protocol")
    if digest(models.model_dump(mode="json")) != development["model_sha256"] or not final_eligible(
        data, spec
    ):
        raise ValueError("Frozen models changed or final holdout is insufficient")
    thresholds = {
        k: PriorityThresholds.model_validate(v) for k, v in development["thresholds"].items()
    }
    final_ids = {m.row_id for m in data.result.membership if m.partition == "final_test"}
    used = (
        set(models.forest.fit_row_ids)
        | set(models.forest.calibration_fit_ids)
        | set(models.forest.calibration_audit_ids)
        | {key for t in thresholds.values() for key in t.fit_row_ids}
    )
    if final_ids & used:
        raise ValueError("Final holdout overlaps fitting or threshold selection")
    rows = sorted([r for r in data.samples if r.row_id in final_ids], key=lambda r: r.row_id)
    final = _cohort_report(data, rows, models, thresholds, spec)
    paired = final["uncertainty"]["paired_deltas"]

    def improvement(name: str) -> bool:
        interval = paired.get("forest_minus_" + name, {})
        return (
            interval.get("lower") is not None
            and interval["lower"] > 0
            and interval.get("valid_replicates", 0) >= 0.9 * spec.bootstrap_repetitions
        )

    justified = all(improvement(name) for name in ("baseline-v1", "weighted-baseline-v1"))
    failures = [
        "Held-out groups are satellite partition proxies, not independently verified fires",
        "No emergency-response or physical severity validity is established",
        "Promotion requires independent model-card/limitations review and registry approval",
    ]
    if not justified:
        failures.append(
            "Paired event uncertainty does not establish improvement over both baselines"
        )
    if not models.forest.probability_supported:
        failures.append("Forest output is an uncalibrated model_score; probability remains null")
    return dict(
        schema_version="evaluation-release-v1",
        dataset_sha256=data.result.dataset_sha256,
        holdout_key=development["holdout_key"],
        evaluation_spec_sha256=spec.sha256,
        final_test_status="opened_once",
        final_test_evaluated=True,
        promotion_performed=False,
        promotion_recommendation="independent_review_candidate" if justified else "baseline_only",
        release_recommendation="baseline_only_until_independent_approval"
        if justified
        else "baseline_only",
        final=final,
        thresholds=development["thresholds"],
        ablation_conclusions={
            "weather_and_climate": "improvement_established"
            if improvement("forest_no_weather_climate")
            else "not_established",
            "climate": "improvement_established"
            if improvement("forest_no_climate")
            else "not_established",
        },
        failures=failures,
    )
