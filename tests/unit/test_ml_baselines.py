"""Synthetic policy/scoring and paired development comparisons; no real performance claims."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import numpy as np
import pytest
from feature_factory import inputs, observation
from split_factory import cohort
from test_api_contract import settings

from firewatch.api.presentation import ArtifactInspector, model_metadata
from firewatch.ml.baselines import WeightedPolicy, components, fit_weighted_policy, weighted_index
from firewatch.ml.evaluate import compare_baselines, ranking_metrics
from firewatch.ml.predict import score_features
from firewatch.ml.splits import create_splits, default_split_spec
from firewatch.ml.train import fit_logistic, logistic_scores
from firewatch.pipeline.runner import PipelineRequest
from firewatch.processing.features import build_features
from firewatch.sources.firms_normalize import digest


def feature(**values):
    result = build_features(*inputs([observation(1)]))[0]
    for name, value in values.items():
        result.values[name] = value
        if name in result.missing_flags:
            result.missing_flags[name] = value is None
    result.feature_hash = digest(result.model_dump(mode="json", exclude={"feature_hash"}))
    return result


def test_frp_baseline_preserves_frozen_explanation_and_scores():
    row = feature(frp_max_mw=75.0)
    scored = score_features([row], "baseline-v1")[0]
    assert scored.priority_score == 75 and scored.priority == "HIGH"
    assert scored.explanation == dict(
        reasons=["Maximum observed FRP 75 MW determines this FRP-only ranking."],
        feature_hash=row.feature_hash,
        method="frp-maximum-only-v1",
        frp_missing=False,
        base_value=0.0,
        contributions={"frp_max_mw": 75.0},
        contribution_units="MW",
        threshold_policy="provisional-display-only",
        warnings=scored.warnings,
    )
    assert scored.escalation_probability is None


@pytest.mark.parametrize(
    "name,low,high",
    [
        ("frp_max_mw", 0.0, 100.0),
        ("wind_ms", 0.0, 15.0),
        ("humidity_pct", 100.0, 0.0),
        ("detections_24h", 1, 21),
    ],
)
def test_index_monotonic_components(name, low, high):
    assert weighted_index(feature(**{name: low})) < weighted_index(feature(**{name: high}))


def test_index_hand_calculated_all_maxima_and_missing_floor():
    complete = feature(frp_max_mw=100.0, humidity_pct=0.0, wind_ms=15.0, detections_24h=21)
    assert weighted_index(complete) == pytest.approx(100)
    empty = feature(frp_max_mw=None, humidity_pct=None, wind_ms=None)
    terms, missing = components(empty, WeightedPolicy())
    assert terms == dict(intensity=0, dryness=0, wind=0, previous_detections=0)
    assert missing == ["intensity", "dryness", "wind"]
    score = score_features([empty], "weighted-baseline-v1")[0]
    assert score.priority_score == 0 and score.escalation_probability is None
    assert score.score_kind == "baseline"
    assert len([f for f in score.quality_flags if f.startswith("weighted_index_missing_")]) == 3
    assert score.warnings == score.quality_flags
    assert score.explanation["threshold_policy"] == "provisional-display-only"
    assert score.explanation["missing_components"] == missing
    assert weighted_index(feature(frp_max_mw=1e308, wind_ms=1e308)) <= 100


@pytest.mark.parametrize("weights", [(1, 0, 0, 0), (-1, 1, 1, 0), (0.5, 0.2, 0.2, 0.1)])
def test_unversioned_weight_change_refused(weights):
    with pytest.raises(ValueError, match="weights"):
        WeightedPolicy(weights=weights)


def test_prespecified_scales_and_missing_training_binding_refused():
    with pytest.raises(ValueError, match="prespecified"):
        WeightedPolicy(frp_reference_mw=50)
    with pytest.raises(ValueError, match="exact training"):
        WeightedPolicy(freeze_basis="training_feature_quantiles")


def test_invalid_feature_hash_or_missingness_cannot_score():
    row = feature()
    row.values["wind_ms"] = 100.0
    with pytest.raises(ValueError, match="integrity"):
        score_features([row], "weighted-baseline-v1")
    row = feature()
    row.missing_flags["wind_ms"] = True
    with pytest.raises(ValueError, match="indicators"):
        weighted_index(row)


def test_metadata_ready_without_model_lookup_and_unknown_model_still_unavailable():
    for name in ("baseline-v1", "weighted-baseline-v1"):
        session = MagicMock()
        result = model_metadata(session, settings(active_scorer=name), ArtifactInspector())
        assert result.status == "ready" and result.scoring_available
        assert not result.artifact_required and not result.probability_supported
        assert result.scorer_version == name
        session.scalars.assert_not_called()


def test_weighted_pipeline_request_is_allowlisted():
    f = feature()
    args = dict(
        schema_version="jobs-v1",
        region_id=f.run_id,
        mode="live",
        as_of=f.as_of,
        window_start=f.as_of - timedelta(hours=48),
        window_end=f.as_of,
        scorer_version="weighted-baseline-v1",
        feature_version="features-v1",
    )
    assert PipelineRequest(**args).scorer_version == "weighted-baseline-v1"
    args["scorer_version"] = "unapproved-logistic"
    with pytest.raises(ValueError, match="Unsupported"):
        PipelineRequest(**args)


def test_order_and_scoring_time_stable_and_no_probability_claim():
    a, b = feature(frp_max_mw=1.0), feature(frp_max_mw=100.0)
    now = datetime.now(UTC)
    result = score_features([a, b], "weighted-baseline-v1", scored_at=now)
    assert result[0].priority_score < result[1].priority_score
    assert all(row.scored_at == now and row.escalation_probability is None for row in result)
    assert score_features([], "weighted-baseline-v1") == []


def test_identical_validation_rows_for_all_methods_with_fixed_training_pipeline():
    samples, catalog, review = cohort()
    result = create_splits(samples, catalog, default_split_spec(), review)
    report = compare_baselines(samples, result)
    item = report["cohorts"][0]
    assert set(item["methods"]) == {"baseline-v1", "weighted-baseline-v1", "logistic-comparison-v1"}
    assert len(item["row_ids"]) == len(item["labels"]) == 12
    assert all(
        len(m["scores"]) == 12 and m["metrics"]["rows"] == 12 for m in item["methods"].values()
    )
    assert len(item["logistic"]["coefficients"][0]) == 31
    assert item["weighted_policy"]["freeze_basis"] == "training_feature_quantiles"
    final = {m.row_id for m in result.membership if m.partition == "final_test"}
    assert not final.intersection(item["row_ids"] + item["logistic"]["fit_row_ids"])
    assert not report["final_test_evaluated"] and not report["probability_supported"]
    assert report == compare_baselines(list(reversed(samples)), result)


def test_holdout_changes_cannot_change_training_statistics_or_coefficients():
    samples, catalog, review = cohort()
    original = create_splits(samples, catalog, default_split_spec(), review)
    before = fit_logistic(samples, original)
    policy = fit_weighted_policy(samples, original)
    changed = deepcopy(samples)
    train_ids = set(before.fit_row_ids)
    for row in changed:
        if row.row_id not in train_ids:
            row.target.feature.values["temperature_c"] = 999.0
            row.target.feature.values["wind_ms"] = 999.0
            row.target.feature.feature_hash = digest(
                row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
            )
            row.label.feature_hash = row.target.feature.feature_hash
    result = create_splits(changed, catalog, default_split_spec(), review)
    after = fit_logistic(changed, result)
    second = fit_weighted_policy(changed, result)
    np.testing.assert_allclose(
        before.pipeline.named_steps["logistic"].coef_,
        after.pipeline.named_steps["logistic"].coef_,
        rtol=0,
        atol=0,
    )
    assert policy.frp_reference_mw == second.frp_reference_mw
    assert policy.wind_reference_ms == second.wind_reference_ms
    with pytest.raises(ValueError, match="sealed"):
        logistic_scores(after, changed, result, role="final_test")
    with pytest.raises(ValueError, match="match"):
        logistic_scores(before, changed, result)


def test_cv_compares_each_sufficient_fold_and_never_consumes_final_test():
    samples, catalog, review = cohort(cv=True)
    result = create_splits(samples, catalog, default_split_spec(), review)
    report = compare_baselines(samples, result)
    assert report["cohort_count"] >= 2
    for fold in report["cohorts"]:
        assert set(fold["row_ids"]).isdisjoint(fold["logistic"]["fit_row_ids"])
    with pytest.raises(ValueError, match="explicit"):
        fit_logistic(samples, result)


def test_sparse_data_skips_fits_and_metrics_instead_of_inventing_performance(monkeypatch):
    samples, catalog, review = cohort(all_positive=True)
    result = create_splits(samples, catalog, default_split_spec(), review)

    def denied(*args, **kwargs):
        raise AssertionError("No fit permitted for insufficient data")

    monkeypatch.setattr("firewatch.ml.evaluate.fit_logistic", denied)
    report = compare_baselines(samples, result)
    assert report["cohorts"] == [] and report["status"] == "insufficient_data"
    assert report["logistic_status"] == "skipped_insufficient_data"
    assert report["release_recommendation"] == "baseline_only"
    samples[0].label.unobserved_followup = True
    with pytest.raises(ValueError, match="changed"):
        compare_baselines(samples, result)


def test_ranking_metrics_ties_and_single_class_are_explicit():
    samples, _, _ = cohort(all_positive=True)
    metrics = ranking_metrics(samples[:2], [0.0, 0.0])
    assert metrics["average_precision"] == 1.0 and metrics["roc_auc"] is None
    samples[0].label.target_value = 0
    samples[0].label.label = "negative"
    samples[1].label.target_value = 0
    samples[1].label.label = "negative"
    metrics = ranking_metrics(samples[:2], [0.0, 0.0])
    assert metrics["average_precision"] is None and metrics["mean_run_recall_at_10"] is None
    assert metrics["runs_without_positives"] == 1
    with pytest.raises(ValueError, match="nonempty identical"):
        ranking_metrics([], [])


def test_tracked_baseline_policy_catalog_matches_built_in_version():
    from pathlib import Path

    import yaml

    from firewatch.ml.baselines import baseline_catalog

    assert yaml.safe_load(Path("configs/baselines-v1.yaml").read_bytes()) == baseline_catalog()


def test_logistic_nonconvergence_fails_closed(monkeypatch):
    import warnings

    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression

    samples, catalog, review = cohort()
    result = create_splits(samples, catalog, default_split_spec(), review)

    def fail(*args, **kwargs):
        warnings.warn("synthetic nonconvergence", ConvergenceWarning, stacklevel=2)

    monkeypatch.setattr(LogisticRegression, "fit", fail)
    with pytest.raises(ValueError, match="did not converge"):
        fit_logistic(samples, result)


def test_logistic_fit_refused_for_baseline_only_and_index_does_not_fit_global_data():
    samples, catalog, review = cohort(all_positive=True)
    result = create_splits(samples, catalog, default_split_spec(), review)
    with pytest.raises(ValueError, match="baseline"):
        fit_logistic(samples, result)
    with pytest.raises(ValueError, match="baseline"):
        fit_weighted_policy(samples, result)


@pytest.mark.parametrize(
    "frp,humidity,wind,priority",
    [
        (0.0, 100.0, 0.0, "LOW"),
        (0.0, 40.0, 15.0, "MEDIUM"),
        (100.0, 40.0, 0.0, "HIGH"),
        (100.0, 40.0, 15.0, "CRITICAL"),
    ],
)
def test_weighted_display_boundaries_are_inclusive_and_provisional(frp, humidity, wind, priority):
    row = feature(frp_max_mw=frp, humidity_pct=humidity, wind_ms=wind)
    result = score_features([row], "weighted-baseline-v1")[0]
    assert result.priority == priority
    assert result.explanation["threshold_policy"] == "provisional-display-only"


def test_logistic_learns_synthetic_training_signal_and_scores_disjoint_validation():
    # Deliberately separable synthetic classifier inputs; no real performance claim.
    samples, catalog, review = cohort()
    for row in samples:
        value = 100.0 if row.label.target_value == 1 else 1.0
        row.target.feature.values["frp_max_mw"] = value
        row.target.feature.values["frp_sum_mw"] = value
        row.target.feature.feature_hash = digest(
            row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
        )
        row.label.feature_hash = row.target.feature.feature_hash
    result = create_splits(samples, catalog, default_split_spec(), review)
    fitted = fit_logistic(samples, result)
    validation, scores = logistic_scores(fitted, samples, result)
    positives = [
        score for row, score in zip(validation, scores, strict=True) if row.label.target_value == 1
    ]
    negatives = [
        score for row, score in zip(validation, scores, strict=True) if row.label.target_value == 0
    ]
    assert min(positives) > max(negatives)
    assert fitted.pipeline.named_steps["logistic"].coef_[0, 0] > 0
    assert set(fitted.fit_row_ids).isdisjoint(row.row_id for row in validation)
