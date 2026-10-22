"""Forest search, leakage boundaries, calibration gates and non-executable artifacts."""

from copy import deepcopy

import numpy as np
import pytest
from forest_factory import forest_data, small_spec

from firewatch.ml.forest_artifact import PortableForest, Sigmoid
from firewatch.ml.forest_spec import ForestSpec
from firewatch.ml.forest_training import ForestData, train_forest
from firewatch.ml.splits import create_splits
from firewatch.processing.features import MODEL_INPUT_NAMES
from firewatch.sources.firms_normalize import digest


def test_bounded_search_compares_class_weights_and_preserves_holdouts():
    data = forest_data()
    run = train_forest(data, small_spec())
    assert run.report["status"] == "fitted_development_only"
    assert len(run.report["candidate_results"]) == 2
    assert set(run.report["class_weight_comparison"]) == {"None", "balanced"}
    assert run.model.feature_names == list(MODEL_INPUT_NAMES)
    reserved = {
        m.row_id for m in data.result.membership if m.partition in {"threshold", "final_test"}
    }
    used = (
        set(run.model.fit_row_ids)
        | set(run.model.calibration_fit_ids)
        | set(run.model.calibration_audit_ids)
    )
    assert not reserved & used
    assert run.report["final_test_status"] == "sealed"
    assert run.report["threshold_selection_status"] == "reserved_untouched"
    assert not run.report["promotion_performed"]
    assert run.model.calibration_status == "insufficient_data"
    assert all(
        p.escalation_probability is None
        for p in run.model.predict([r.target.feature for r in data.samples])
    )
    positives = [r for r in data.samples if r.label.target_value]
    negatives = [r for r in data.samples if not r.label.target_value]
    assert (
        run.model.predict([positives[0].target.feature])[0].model_score
        > run.model.predict([negatives[0].target.feature])[0].model_score
    )


def test_model_json_roundtrip_and_repeatability():
    data = forest_data()
    run = train_forest(data, small_spec())
    second = train_forest(
        ForestData(
            list(reversed(data.samples)), data.result, data.split_spec, data.catalog, data.review
        ),
        small_spec(),
    )
    assert run.report == second.report and run.model == second.model
    restored = PortableForest.model_validate_json(run.model.model_dump_json())
    assert restored.predict([r.target.feature for r in data.samples]) == run.model.predict(
        [r.target.feature for r in data.samples]
    )


def test_calibration_accepted_only_with_independent_non_degrading_audit():
    data = forest_data(calibration="accepted")
    run = train_forest(data, small_spec())
    cal = run.report["calibration"]
    assert cal["status"] == "accepted" and run.model.probability_supported
    assert cal["threshold_selection_status"] == "reserved_untouched"
    assert set(cal["fit_row_ids"]).isdisjoint(cal["audit_row_ids"])
    meta = {m.row_id: m for m in data.result.membership}
    assert {meta[key].event_id for key in cal["fit_row_ids"]}.isdisjoint(
        {meta[key].event_id for key in cal["audit_row_ids"]}
    )
    assert cal["calibrated_audit"]["brier"] <= cal["uncalibrated_audit"]["brier"]
    assert sum(b["rows"] for b in cal["calibrated_audit"]["reliability"]) == len(
        cal["audit_row_ids"]
    )
    assert run.model.sigmoid.a < 0
    for p in run.model.predict([r.target.feature for r in data.samples[:5]]):
        assert 0 <= p.escalation_probability <= 1 and p.score_kind == "calibrated_probability"


def test_degrading_sigmoid_is_recorded_and_rejected():
    run = train_forest(forest_data(calibration="rejected"), small_spec())
    assert run.report["calibration"]["status"] == "rejected"
    assert run.model.sigmoid is None and not run.model.probability_supported
    assert (
        run.report["calibration"]["calibrated_audit"]["brier"]
        > run.report["calibration"]["uncalibrated_audit"]["brier"]
    )


def test_forward_cv_selection_stays_uncalibrated_without_spending_final_holdout():
    data = forest_data(cv=True)
    run = train_forest(data, small_spec())
    assert run.model.calibration_status == "cv_uncalibrated"
    assert run.report["threshold_selection_status"] == "requires_cross_fitted_threshold_selection"
    assert (
        run.report["calibration"]["threshold_selection_status"]
        == "requires_cross_fitted_threshold_selection"
    )
    assert run.model.fold_id == [f.fold_id for f in data.result.folds if f.sufficient][-1]
    final = {m.row_id for m in data.result.membership if m.partition == "final_test"}
    for candidate in run.report["candidate_results"]:
        assert len(candidate["cohorts"]) >= 2
        for fold in candidate["cohorts"]:
            assert not final.intersection(fold["fit_row_ids"] + fold["validation_row_ids"])


def test_insufficient_data_never_fits_a_classifier(monkeypatch):
    import firewatch.ml.forest_training as module

    def denied(*args, **kwargs):
        raise AssertionError("Insufficient data must not fit")

    monkeypatch.setattr(module, "_fit_pipeline", denied)
    run = train_forest(forest_data(all_positive=True), small_spec())
    assert run.model is None and run.report["status"] == "insufficient_data"
    assert run.report["candidate_results"] == [] and not run.report["probability_supported"]


def test_final_test_and_threshold_changes_cannot_affect_selection_or_fitted_model():
    data = forest_data()
    before = train_forest(data, small_spec())
    changed = deepcopy(data.samples)
    reserved = {
        m.row_id for m in data.result.membership if m.partition in {"threshold", "final_test"}
    }
    for row in changed:
        if row.row_id in reserved:
            row.target.feature.values["frp_max_mw"] = 50000.0
            row.target.feature.values["wind_ms"] = 50000.0
            row.target.feature.feature_hash = digest(
                row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
            )
            row.label.feature_hash = row.target.feature.feature_hash
    result = create_splits(changed, data.catalog, data.split_spec, data.review)
    after = train_forest(
        ForestData(changed, result, data.split_spec, data.catalog, data.review), small_spec()
    )
    assert before.report["selected_parameters"] == after.report["selected_parameters"]
    assert before.report["candidate_results"] == after.report["candidate_results"]
    assert before.model.trees == after.model.trees and before.model.numeric == after.model.numeric


@pytest.mark.parametrize(
    "change",
    ["trees", "depth", "leaf", "grid_duplicates", "weights", "purge", "seed", "split_fraction"],
)
def test_invalid_or_unbounded_training_policies_rejected(change):
    config = small_spec().model_dump()
    key, value = {
        "trees": ("tree_counts", [1000]),
        "depth": ("depths", [100]),
        "leaf": ("leaf_sizes", [0]),
        "grid_duplicates": ("tree_counts", [8, 8]),
        "weights": ("class_weights", [None, None]),
        "purge": ("calibration_purge_hours", 24),
        "seed": ("random_seed", 1),
        "split_fraction": ("calibration_split_fraction", 0.7),
    }[change]
    config[key] = value
    with pytest.raises(ValueError):
        ForestSpec.model_validate(config)


@pytest.mark.parametrize(
    "change", ["cycle", "feature", "scale", "order", "claim", "overlap", "unreachable", "target"]
)
def test_malformed_forest_artifacts_cannot_load(change):
    model = train_forest(forest_data(), small_spec()).model
    raw = model.model_dump()
    if change == "cycle":
        raw["trees"][0]["nodes"][0]["left"] = 0
    elif change == "feature":
        raw["trees"][0]["nodes"][0]["feature"] = 1000
    elif change == "scale":
        raw["numeric"]["scales"][0] = 0
    elif change == "order":
        raw["feature_names"].reverse()
    elif change == "claim":
        raw["probability_supported"] = True
    elif change == "overlap":
        raw["calibration_fit_ids"] = raw["fit_row_ids"][:1]
    elif change == "target":
        raw["target_spec_sha256"] = "0" * 64
    else:
        raw["trees"][0]["nodes"].append(raw["trees"][0]["nodes"][-1])
    with pytest.raises(ValueError):
        PortableForest.model_validate(raw)


def test_feature_integrity_and_config_checked_before_prediction():
    data = forest_data()
    model = train_forest(data, small_spec()).model
    row = deepcopy(data.samples[0].target.feature)
    row.values["frp_max_mw"] = 300.0
    with pytest.raises(ValueError, match="integrity"):
        model.predict([row])
    row.feature_hash = digest(row.model_dump(mode="json", exclude={"feature_hash"}))
    row.lineage["config"]["weather_max_age_hours"] = 4
    row.feature_hash = digest(row.model_dump(mode="json", exclude={"feature_hash"}))
    with pytest.raises(ValueError, match="configuration"):
        model.predict([row])


def test_sigmoid_stable_and_monotonic():
    value = Sigmoid(a=-1e6, b=1e6)
    scores = [value.apply(x) for x in (0.0, 0.5, 1.0)]
    assert np.all(np.isfinite(scores)) and scores == sorted(scores)
    with pytest.raises(ValueError):
        Sigmoid(a=1, b=0)


def test_class_weight_is_selected_using_validation_diagnostics():
    data = forest_data()
    train_ids = {m.row_id for m in data.result.membership if m.partition == "train"}
    flipped = 0
    for row in data.samples:
        if row.row_id in train_ids:
            row.target.feature.values["frp_max_mw"] = 10.0
            row.target.feature.values["frp_sum_mw"] = 10.0
            if row.label.target_value == 1 and flipped < 4:
                row.label.target_value = 0
                row.label.label = "negative"
                flipped += 1
            row.target.feature.feature_hash = digest(
                row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
            )
            row.label.feature_hash = row.target.feature.feature_hash
    result = create_splits(data.samples, data.catalog, data.split_spec, data.review)
    run = train_forest(
        ForestData(data.samples, result, data.split_spec, data.catalog, data.review), small_spec()
    )
    assert run.report["selected_parameters"]["class_weight"] == "balanced"
    assert run.report["estimator_parameters"]["random_state"] == 42
    assert run.report["estimator_parameters"]["bootstrap"] is True


def test_whole_source_event_crossing_internal_calibration_boundary_is_excluded():
    from datetime import UTC, datetime, timedelta

    from split_factory import reviewed, sample

    from firewatch.ml.events import build_event_catalog

    data = forest_data(calibration="accepted")
    observations = list(data.catalog.observations)
    # Extend the June 26 event with unlabelled observations through the internal boundary.
    target = next(
        row for row in data.samples if row.target.feature.as_of == datetime(2024, 6, 26, tzinfo=UTC)
    )
    old_id = next(m.event_id for m in data.result.membership if m.row_id == target.row_id)
    lat = next(g.latitude for g in data.catalog.groups if g.event_id == old_id)
    for i in range(1, 6):
        _, obs = sample(
            90000 + i, datetime(2024, 6, 27, tzinfo=UTC) + timedelta(hours=36 * i), latitude=lat
        )
        observations.append(obs)
    catalog = build_event_catalog(observations, data.catalog.source_manifest_sha256)
    review = reviewed(catalog)
    result = create_splits(data.samples, catalog, data.split_spec, review)
    run = train_forest(
        ForestData(data.samples, result, data.split_spec, catalog, review), small_spec()
    )
    assert target.row_id in run.report["calibration"]["excluded_row_ids"]
    assert run.report["calibration"]["status"] == "insufficient_data"


def test_changing_calibration_labels_never_changes_selected_forest():
    accepted = train_forest(forest_data(calibration="accepted"), small_spec())
    rejected = train_forest(forest_data(calibration="rejected"), small_spec())
    assert accepted.report["selected_parameters"] == rejected.report["selected_parameters"]
    assert accepted.model.trees == rejected.model.trees
    assert accepted.model.numeric == rejected.model.numeric


def test_portable_threshold_comparison_preserves_float32_input_and_double_threshold():
    from firewatch.ml.forest_artifact import ForestNode, ForestTree, NumericTransform

    data = forest_data()
    model = train_forest(data, small_spec()).model

    def node(left, right, feature, threshold, values):
        return ForestNode(
            left=left,
            right=right,
            feature=feature,
            threshold=threshold,
            class_values=values,
            impurity=0,
            samples=1,
            weighted_samples=1,
        )

    tree = ForestTree(
        nodes=[
            node(1, 2, 0, 1.00000008, (0.5, 0.5)),
            node(-1, -1, -2, -2, (1, 0)),
            node(-1, -1, -2, -2, (0, 1)),
        ]
    )
    model.trees = [tree for _ in model.trees]
    model.numeric = NumericTransform(medians=[0.0] * 17, means=[0.0] * 17, scales=[1.0] * 17)
    rows = [deepcopy(data.samples[0].target.feature) for _ in range(2)]
    for row, value in zip(
        rows, [1.0, float(np.nextafter(np.float32(1.0), np.float32(2.0)))], strict=True
    ):
        row.values["frp_max_mw"] = value
        row.feature_hash = digest(row.model_dump(mode="json", exclude={"feature_hash"}))
    assert [p.model_score for p in model.predict(rows)] == [0.0, 1.0]


def test_all_missing_training_measurements_retain_zero_imputation_and_flags():
    from firewatch.processing.features import FEATURE_NAMES

    data = forest_data()
    for row in data.samples:
        for name in ("wind_ms", "humidity_pct"):
            row.target.feature.values[name] = None
            row.target.feature.missing_flags[name] = True
        row.target.feature.feature_hash = digest(
            row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
        )
        row.label.feature_hash = row.target.feature.feature_hash
    result = create_splits(data.samples, data.catalog, data.split_spec, data.review)
    run = train_forest(
        ForestData(data.samples, result, data.split_spec, data.catalog, data.review), small_spec()
    )
    assert run.model.numeric.medians[FEATURE_NAMES.index("wind_ms")] == 0
    assert len(run.model.feature_names) == 31
    assert all(
        np.isfinite(p.model_score)
        for p in run.model.predict([r.target.feature for r in data.samples])
    )


def test_constant_forest_scores_do_not_claim_calibrated_probability():
    data = forest_data(calibration="accepted")
    for row in data.samples:
        row.target.feature.values["frp_max_mw"] = 10.0
        row.target.feature.values["frp_sum_mw"] = 10.0
        row.target.feature.feature_hash = digest(
            row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
        )
        row.label.feature_hash = row.target.feature.feature_hash
    result = create_splits(data.samples, data.catalog, data.split_spec, data.review)
    run = train_forest(
        ForestData(data.samples, result, data.split_spec, data.catalog, data.review), small_spec()
    )
    assert run.report["calibration"]["reason"] == "Insufficient calibration score variation"
    assert run.model.sigmoid is None and not run.model.probability_supported


def test_forged_partition_membership_is_rejected_before_fit(monkeypatch):
    data = forest_data()
    next(m for m in data.result.membership if m.partition == "final_test").partition = "train"

    def denied(*args, **kwargs):
        raise AssertionError("Forged membership must not fit")

    monkeypatch.setattr("firewatch.ml.forest_training._fit_pipeline", denied)
    with pytest.raises(ValueError, match="recomputed"):
        train_forest(data, small_spec())
