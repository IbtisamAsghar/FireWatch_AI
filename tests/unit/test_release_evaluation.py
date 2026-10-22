"""Synthetic evaluation evidence; these fixtures are not observed-fire performance claims."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from forest_factory import forest_data, small_spec
from split_factory import reviewed, sample

from firewatch.ml.evaluation_artifacts import write_model_card
from firewatch.ml.evaluation_models import EvaluationModels
from firewatch.ml.evaluation_spec import EvaluationSpec
from firewatch.ml.events import build_event_catalog
from firewatch.ml.final_test_ledger import FinalTestLedger
from firewatch.ml.forest_training import ForestData, train_forest
from firewatch.ml.priority_thresholds import baseline_thresholds, choose_thresholds
from firewatch.ml.release_evaluation import holdout_key, prepare_report, release_report
from firewatch.ml.release_metrics import EvaluationRow, breakdowns, event_bootstrap, metrics
from firewatch.ml.splits import create_splits
from firewatch.sources.firms_normalize import digest


def frame(labels):
    return [
        EvaluationRow(
            str(i),
            f"event-{i // 2}",
            f"run-{i // 2}",
            "2024-08",
            "VIIRS:NOAA20",
            "complete",
            "false",
            y,
        )
        for i, y in enumerate(labels)
    ]


def test_classification_ranking_and_probability_definitions():
    rows = frame([1, 0, 0, 0])
    result = metrics(rows, [0.8, 0.9, 0.2, 0.1], 0.5, [0.8, 0.9, 0.2, 0.1])
    assert result["confusion_matrix"]["values"] == [[2, 1], [0, 1]]
    assert result["precision"] == 0.5 and result["recall"] == 1
    assert result["f1"] == pytest.approx(2 / 3)
    assert result["prevalence"] == 0.25
    assert result["average_precision"] == 0.5
    assert result["pr_auc"] == 0.25
    assert result["precision_at_10"] == 0.25
    assert result["recall_at_10"] == 1
    assert result["runs_without_positives"] == 1 and result["recall_runs"] == 1
    assert result["probability"]["brier"] == pytest.approx(0.225)
    assert sum(b["rows"] for b in result["probability"]["reliability"]) == 4
    assert set(breakdowns(rows, [0.8, 0.9, 0.2, 0.1], 0.5)) == {
        "event_id",
        "month",
        "sensor_product",
        "weather_completeness",
        "unobserved_followup",
    }


def test_top_ten_is_per_run_and_has_stable_ties():
    rows = [replace(r, run_id="one") for r in frame([1] * 10 + [0] * 2)]
    result = metrics(rows, list(range(12)), 5)
    assert result["precision_at_10"] == 0.8
    assert result["recall_at_10"] == 0.8
    assert metrics(frame([0, 0]), [0, 0], 1)["recall_at_10"] is None


@pytest.mark.parametrize(
    "scores,labels,prob",
    [
        ([1], [1, 0], None),
        ([float("nan"), 0], [1, 0], None),
        ([1, 0], [2, 0], None),
        ([1, 0], [1, 0], [1.1, 0]),
    ],
)
def test_invalid_metrics_fail_closed(scores, labels, prob):
    with pytest.raises(ValueError):
        metrics(frame(labels), scores, 0.5, prob)


def test_thresholds_are_development_bound_and_scale_specific():
    ids = [digest(i) for i in range(4)]
    policy = choose_thresholds(
        "forest", "forest_vote_fraction", ids, [0, 1, 0, 1], [0.1, 0.9, 0.2, 0.9]
    )
    assert policy.fit_row_ids == sorted(ids) and policy.provisional
    assert policy.degenerate_bands and policy.high == 0.9
    assert policy.priority(0.1) == "LOW" and policy.priority(0.9) == "CRITICAL"
    assert baseline_thresholds("baseline-v1").high == 50
    assert baseline_thresholds("weighted-baseline-v1").score_units == "index_0_100"
    with pytest.raises(ValueError):
        baseline_thresholds("forest")
    with pytest.raises(ValueError):
        choose_thresholds("forest", "score", ids, [1] * 4, [0.2] * 4)


def test_whole_event_bootstrap_is_paired_reproducible_and_honest():
    rows = frame([1, 0] * 5)
    policies = {n: baseline_thresholds("baseline-v1") for n in ("forest", "baseline-v1")}
    methods = {"forest": [100, 0] * 5, "baseline-v1": [0, 100] * 5}
    spec = EvaluationSpec(bootstrap_repetitions=20)
    result = event_bootstrap(rows, methods, policies, spec)
    assert result == event_bootstrap(rows, methods, policies, spec)
    assert result["resampling_unit"] == "whole_partition_event"
    assert result["paired_deltas"]["forest_minus_baseline-v1"]["lower"] == 0.5
    assert result["paired_deltas"]["forest_minus_baseline-v1"]["valid_replicates"] == 20
    assert (
        event_bootstrap(rows[:2], {n: v[:2] for n, v in methods.items()}, policies, spec)["status"]
        == "insufficient_events"
    )
    with pytest.raises(ValueError):
        event_bootstrap(rows[:2], methods, policies, spec)


@pytest.fixture(scope="module")
def prepared():
    data, config = forest_data(calibration="accepted"), small_spec()
    run = train_forest(data, config)
    spec = EvaluationSpec(bootstrap_repetitions=20)
    report, models = prepare_report(data, run.model, config, spec)
    return data, config, spec, report, models


def test_development_freeze_ablations_and_portable_predictions(prepared):
    data, _, _, report, models = prepared
    assert report["status"] == "development_frozen" and models is not None
    assert report["final_test_status"] == "sealed"
    assert len(report["development"]["methods"]) == 6
    thresholds = set(next(iter(report["thresholds"].values()))["fit_row_ids"])
    assert not thresholds & set(models.forest.fit_row_ids)
    assert not thresholds & set(models.forest.calibration_fit_ids)
    assert not thresholds & set(models.forest.calibration_audit_ids)
    final = {m.row_id for m in data.result.membership if m.partition == "final_test"}
    assert not final & thresholds
    assert not final & set(models.logistic_fit_row_ids)
    mask = models.ablation_features["forest_no_weather_climate"]
    assert not any("wind" in n or "temperature" in n or "rainfall" in n for n in mask)
    clone = EvaluationModels.model_validate_json(models.model_dump_json())
    assert clone.predictions(data.samples) == models.predictions(data.samples)


def test_calibrated_reliability_card_is_deterministic(prepared, tmp_path):
    *_, report, models = prepared
    assert models.forest.probability_supported
    write_model_card(tmp_path, report)
    svg = tmp_path / "reliability-forest.svg"
    first = svg.read_bytes()
    assert b"Observed cohort" in first
    write_model_card(tmp_path, report)
    assert svg.read_bytes() == first
    assert "not physical wildfire severity" in (tmp_path / "model-card.md").read_text()


def test_sparse_data_never_opens_final_test(tmp_path):
    data, config = forest_data(all_positive=True), small_spec()
    report, model = prepare_report(data, None, config, EvaluationSpec())
    assert report["status"] == "insufficient_data" and model is None
    assert report["thresholds"] == {} and report["development"] is None
    write_model_card(tmp_path, report)
    assert not list(tmp_path.glob("*.svg"))


def test_nested_cv_keeps_threshold_fold_out_of_parameter_selection():
    data, config = forest_data(cv=True), small_spec()
    run = train_forest(data, config)
    report, models = prepare_report(
        data, run.model, config, EvaluationSpec(bootstrap_repetitions=20)
    )
    assert report["status"] == "development_frozen"
    assert not models.forest.probability_supported
    threshold_ids = set(report["development"]["row_ids"])
    assert not threshold_ids & set(models.logistic_fit_row_ids)
    assert report["selection_evidence"]


def final_data():
    base = forest_data(calibration="accepted")
    rows = list(base.samples)
    observations = list(base.catalog.observations)
    for group in range(4):
        for j in range(6):
            row, obs = sample(
                500 + group * 10 + j,
                datetime(2024, 9, 5, tzinfo=UTC) + timedelta(days=group * 7, hours=j * 6),
                latitude=5 + group * 0.2,
                positive=j % 2 == 0,
            )
            row.target.feature.values["frp_max_mw"] = 100.0 if j % 2 == 0 else 1.0
            row.target.feature.values["frp_sum_mw"] = row.target.feature.values["frp_max_mw"]
            row.target.feature.feature_hash = digest(
                row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
            )
            row.label.feature_hash = row.target.feature.feature_hash
            rows.append(row)
            observations.append(obs)
    catalog = build_event_catalog(observations, "a" * 64)
    review = reviewed(catalog)
    result = create_splits(rows, catalog, base.split_spec, review)
    return ForestData(rows, result, base.split_spec, catalog, review)


def test_one_shot_ledger_race_failure_and_binding(tmp_path):
    ledger = FinalTestLedger(tmp_path / "claims.sqlite3")

    def claim(i):
        try:
            ledger.claim("same-holdout", str(i), tmp_path / str(i))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(claim, [0, 1])) == 1
    saved = ledger.get("same-holdout")
    ledger.fail("same-holdout")
    assert FinalTestLedger(ledger.path).get("same-holdout")["state"] == "failed"
    with pytest.raises(ValueError, match="already claimed"):
        ledger.claim("same-holdout", "different-model", tmp_path / "other")
    with pytest.raises(ValueError):
        ledger.complete("same-holdout", "wrong", tmp_path / "other", "abc")
    ledger.complete(
        "same-holdout",
        saved["prepared_sha256"],
        Path(saved["output_path"]),
        "result",
    )
    assert ledger.get("same-holdout")["state"] == "complete"


def test_final_insufficient_and_changed_model_refused(prepared):
    data, _, spec, report, models = prepared
    with pytest.raises(ValueError, match="insufficient"):
        release_report(data, report, models, spec)
    assert holdout_key(data) == holdout_key(forest_data(cv=True))


@pytest.fixture(scope="module")
def release_ready():
    data, config = final_data(), small_spec()
    spec = EvaluationSpec(bootstrap_repetitions=20)
    trained = train_forest(data, config)
    development, models = prepare_report(data, trained.model, config, spec)
    return data, spec, development, models


def test_final_release_uses_frozen_thresholds_and_all_six_methods(release_ready):
    data, spec, development, models = release_ready
    report = release_report(data, development, models, spec)
    assert report["final_test_status"] == "opened_once"
    assert report["thresholds"] == development["thresholds"]
    assert len(report["final"]["methods"]) == 6
    assert report["final"]["uncertainty"]["events"] == 6
    assert not report["promotion_performed"]
    assert report["release_recommendation"] == "baseline_only"
    for method in report["final"]["methods"].values():
        assert len(method["scores"]) == len(report["final"]["labels"])
    changed = models.model_copy(deep=True)
    changed.logistic.intercept += 1
    with pytest.raises(ValueError, match="changed"):
        release_report(data, development, changed, spec)


@pytest.mark.parametrize("failure", [None, "scoring", "completion"])
def test_final_export_claims_before_scoring_and_recovers_only_saved_result(
    release_ready, tmp_path, monkeypatch, failure
):
    """Unit control-flow test; source verification is independently integration-tested."""
    import hashlib
    import json

    import firewatch.ml.evaluation_package as package

    data, spec, development, models = release_ready
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    for name, value in {
        "evaluation-report.json": development,
        "evaluation-spec.json": spec.model_dump(mode="json"),
        "evaluation-models.json": models.model_dump(mode="json"),
    }.items():
        (prepared / name).write_text(json.dumps(value))
    (prepared / "manifest.json").write_text(
        json.dumps(
            {
                "files": {
                    n: {}
                    for n in (
                        "evaluation-report.json",
                        "evaluation-spec.json",
                        "evaluation-models.json",
                    )
                }
            }
        )
    )
    sha = hashlib.sha256((prepared / "manifest.json").read_bytes()).hexdigest()
    output, ledger_path = tmp_path / "released", tmp_path / "ledger.sqlite3"

    def verification(root, expected=None):
        return {
            "stage": "development" if root == prepared else "final",
            "manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        }

    monkeypatch.setattr(package, "verify_evaluation", verification)
    monkeypatch.setattr(package, "read_forest_data", lambda root: data)
    original = package.release_report
    calls = []

    def scoring(*args):
        assert FinalTestLedger(ledger_path).get(development["holdout_key"])["state"] == "claimed"
        calls.append(True)
        if failure == "scoring":
            raise ValueError("synthetic scoring failure")
        return original(*args)

    monkeypatch.setattr(package, "release_report", scoring)
    complete = FinalTestLedger.complete
    if failure == "completion":

        def fail_complete(*args):
            raise ValueError("synthetic completion failure")

        monkeypatch.setattr(FinalTestLedger, "complete", fail_complete)
    if failure:
        with pytest.raises(ValueError, match="synthetic"):
            package.export_final(prepared, output, sha, ledger_path)
        assert FinalTestLedger(ledger_path).get(development["holdout_key"])["state"] == "failed"
        monkeypatch.setattr(FinalTestLedger, "complete", complete)
        if failure == "scoring":
            with pytest.raises(ValueError, match="already claimed"):
                package.export_final(prepared, output, sha, ledger_path)
            assert not output.exists()
            return
    else:
        package.export_final(prepared, output, sha, ledger_path)
    assert output.exists()
    recovered = package.export_final(prepared, output, sha, ledger_path)
    assert recovered["final_test_status"] == "opened_once"
    assert len(calls) == 1
    assert FinalTestLedger(ledger_path).get(development["holdout_key"])["state"] == "complete"
    with pytest.raises(ValueError, match="already claimed"):
        package.export_final(prepared, tmp_path / "second-experiment", sha, ledger_path)
    assert not list(tmp_path.glob(".release-evaluation-*"))
