"""No shared events/support, sparse fallback and training-only preprocessing."""

from copy import deepcopy
from datetime import timedelta

import numpy as np
import pytest
from split_factory import cohort, reviewed, sample

from firewatch.ml.events import build_event_catalog
from firewatch.ml.preprocessing import development_rows, fit_preprocessor, transform_development
from firewatch.ml.splits import PARTITIONS, create_splits, default_split_spec
from firewatch.sources.firms_normalize import digest


def test_five_chronological_roles_are_event_disjoint_and_sealed():
    samples, catalog, review = cohort()
    result = create_splits(samples, catalog, default_split_spec(), review)
    assert result.strategy == "chronological" and result.training_ready
    assert all(result.partition_counts[name]["events"] >= 2 for name in PARTITIONS)
    sets = [{m.event_id for m in result.membership if m.partition == name} for name in PARTITIONS]
    assert not any(a & b for i, a in enumerate(sets) for b in sets[i + 1 :])
    assert (
        result.final_test_status == "sealed"
        and result.release_recommendation == "await_model_evaluation"
    )
    assert create_splits(list(reversed(samples)), catalog, default_split_spec(), review) == result


@pytest.mark.parametrize(
    "hours,excluded", [(-48, True), (0, True), (48, True), (-48.0001, False), (48.0001, False)]
)
def test_boundary_purge_is_closed_and_at_least_48_hours_each_side(hours, excluded):
    spec = default_split_spec()
    boundary = spec.blocks[2].start
    row, obs = sample(999, boundary + timedelta(hours=hours), positive=True)
    catalog = build_event_catalog([obs], "a" * 64)
    result = create_splits([row], catalog, spec, reviewed(catalog))
    assert ("boundary_purge" in result.membership[0].exclusion_reasons) == excluded


def test_whole_event_crossing_boundary_is_removed_even_if_only_one_side_has_rows():
    spec = default_split_spec()
    boundary = spec.blocks[2].start
    row, obs = sample(999, boundary - timedelta(days=4), positive=True)
    # A chain of unlabelled source observations connects the event into the next block.
    extra = []
    for i in range(1, 7):
        _, point = sample(
            1000 + i, boundary - timedelta(days=4) + timedelta(hours=36 * i), positive=True
        )
        extra.append(point)
    catalog = build_event_catalog([obs, *extra], "a" * 64)
    result = create_splits([row], catalog, spec, reviewed(catalog))
    assert result.membership[0].partition is None
    assert "cross_boundary_event" in result.membership[0].exclusion_reasons


def test_sparse_calibration_uses_only_forward_grouped_cv_and_retains_holdout():
    samples, catalog, review = cohort(cv=True)
    result = create_splits(samples, catalog, default_split_spec(), review)
    assert result.strategy == "grouped_temporal_cv" and result.training_ready
    assert result.final_holdout_usable and result.development_cv_ready
    final = {m.row_id for m in result.membership if m.partition == "final_test"}
    for fold in result.folds:
        assert not final.intersection(fold.train_ids + fold.validation_ids)
        assert not set(fold.train_ids) & set(fold.validation_ids)


def test_cv_without_usable_holdout_keeps_baseline_release_recommendation():
    samples, catalog, review = cohort(cv=True, holdout=False)
    result = create_splits(samples, catalog, default_split_spec(), review)
    assert result.strategy == "grouped_temporal_cv" and result.development_cv_ready
    assert not result.training_ready and result.release_recommendation == "baseline_only"


def test_missing_classes_or_missing_review_never_claim_supervised_readiness():
    samples, catalog, review = cohort(all_positive=True)
    result = create_splits(samples, catalog, default_split_spec(), review)
    assert result.strategy == "baseline_only" and not result.training_ready
    samples, catalog, _ = cohort()
    result = create_splits(samples, catalog, default_split_spec())
    assert result.strategy == "baseline_only" and not result.event_review_complete


@pytest.mark.parametrize(
    "reason", ["off_six_hour_cadence", "unknown_target", "missing_alias", "feature_config"]
)
def test_excluded_or_unbound_rows_do_not_enter_a_partition(reason):
    samples, catalog, review = cohort()
    row = samples[0]
    if reason == "off_six_hour_cadence":
        row.label.dataset_exclusions.append(reason)
    elif reason == "unknown_target":
        row.label.label = "unknown"
        row.label.target_value = None
    elif reason == "missing_alias":
        row.target.feature.lineage["member_revisions"][0]["revision_hash"] = "0" * 64
        row.target.feature.feature_hash = digest(
            row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
        )
        row.label.feature_hash = row.target.feature.feature_hash
    else:
        row.target.feature.lineage["config"]["weather_max_age_hours"] = 4
        row.target.feature.feature_hash = digest(
            row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
        )
        row.label.feature_hash = row.target.feature.feature_hash
    result = create_splits(samples, catalog, default_split_spec(), review)
    actual = next(m for m in result.membership if m.row_id == row.row_id)
    assert actual.partition is None and actual.exclusion_reasons


@pytest.mark.parametrize(
    "field,value",
    [
        ("purge_hours_each_side", 24),
        ("feature_lookback_hours", 12),
        ("feature_spec_sha256", "0" * 64),
        ("target_spec_sha256", "0" * 64),
    ],
)
def test_incompatible_frozen_split_settings_rejected(field, value):
    spec = default_split_spec()
    data = spec.model_dump()
    data[field] = value
    with pytest.raises(ValueError):
        type(spec).model_validate(data)


def test_overlapping_blocks_and_future_cv_boundaries_rejected():
    spec = default_split_spec()
    spec.blocks[1].start = spec.blocks[0].start
    with pytest.raises(ValueError):
        create_splits([], build_event_catalog([], "a" * 64), spec)
    spec = default_split_spec()
    spec.cv_boundaries[-1] += timedelta(days=1)
    with pytest.raises(ValueError):
        type(spec).model_validate(spec.model_dump())


def test_duplicate_rows_and_conflicting_label_feature_binding_fail():
    samples, catalog, review = cohort()
    with pytest.raises(ValueError, match="Duplicate"):
        create_splits([samples[0], samples[0]], catalog, default_split_spec(), review)
    samples[0].label.feature_hash = "0" * 64
    with pytest.raises(ValueError, match="exact"):
        create_splits(samples, catalog, default_split_spec(), review)


def test_preprocessing_fit_never_sees_holdout_or_validation_statistics():
    samples, catalog, review = cohort()
    result = create_splits(samples, catalog, default_split_spec(), review)
    roles = {m.row_id: m.partition for m in result.membership}
    original = deepcopy(samples)
    for row in samples:
        feature = row.target.feature
        if roles[row.row_id] != "train":
            feature.values["temperature_c"] = 10000.0
        feature.feature_hash = digest(feature.model_dump(mode="json", exclude={"feature_hash"}))
        row.label.feature_hash = feature.feature_hash
    changed = create_splits(samples, catalog, default_split_spec(), review)
    before = fit_preprocessor(original, result)
    after = fit_preprocessor(samples, changed)
    np.testing.assert_array_equal(
        before.transformer.named_transformers_["numeric"].named_steps["impute"].statistics_,
        after.transformer.named_transformers_["numeric"].named_steps["impute"].statistics_,
    )
    assert transform_development(after, samples, changed, role="validation").shape[1] == 31
    assert "event_id" not in after.feature_names
    for role in ("final_test", "test", "all"):
        with pytest.raises(ValueError, match="sealed"):
            development_rows(samples, changed, role=role)


def test_preprocessing_cv_requires_explicit_fold_and_refuses_changed_data():
    samples, catalog, review = cohort(cv=True)
    result = create_splits(samples, catalog, default_split_spec(), review)
    with pytest.raises(ValueError, match="explicit"):
        fit_preprocessor(samples, result)
    fold = next(f for f in result.folds if f.sufficient)
    fitted = fit_preprocessor(samples, result, fold_id=fold.fold_id)
    assert (
        transform_development(
            fitted, samples, result, role="validation", fold_id=fold.fold_id
        ).shape[1]
        == 31
    )
    samples[0].label.unobserved_followup = True
    with pytest.raises(ValueError, match="changed"):
        fit_preprocessor(samples, result, fold_id=fold.fold_id)


def test_no_preprocessing_for_baseline_only_output():
    samples, catalog, review = cohort(all_positive=True)
    result = create_splits(samples, catalog, default_split_spec(), review)
    with pytest.raises(ValueError, match="baseline"):
        fit_preprocessor(samples, result)


def test_split_rechecks_cadence_even_if_caller_omits_label_exclusion():
    spec = default_split_spec()
    row, point = sample(12345, spec.blocks[0].start + timedelta(days=10, hours=1))
    assert row.label.dataset_exclusions == []
    catalog = build_event_catalog([point], "a" * 64)
    result = create_splits([row], catalog, spec, reviewed(catalog))
    assert result.membership[0].partition is None
    assert "off_six_hour_cadence" in result.membership[0].exclusion_reasons


def test_split_refuses_contradictory_label_classes_or_product_bindings():
    samples, catalog, review = cohort()
    samples[0].label.target_value = 1 - samples[0].label.target_value
    with pytest.raises(ValueError, match="class and target"):
        create_splits(samples, catalog, default_split_spec(), review)
    samples[0].label.target_value = 1 - samples[0].label.target_value
    samples[0].label.baseline_products = {}
    with pytest.raises(ValueError, match="source products"):
        create_splits(samples, catalog, default_split_spec(), review)
