"""Exact target thresholds, time/radius boundaries, missingness and frozen design."""

import json
import math
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from cluster_factory import T
from label_factory import current, followup, later

from firewatch.ml.dataset import label_report, load_design, plan_design
from firewatch.ml.labels import (
    FollowupEvidence,
    Outage,
    TargetSpec,
    followup_days,
    label_snapshot,
    load_target_spec,
    on_cadence,
    training_cutoffs,
)
from firewatch.processing.clusters import EARTH_RADIUS_KM
from firewatch.schemas.common import JobStatus, Product, Sensor
from firewatch.sources.firms_normalize import digest


@pytest.mark.parametrize(
    "baseline,future,expected",
    [
        (0, 5, "negative"),
        (0, 5.001, "positive"),
        (10, 15, "negative"),
        (10, 15.0001, "positive"),
        (25, 30, "negative"),
        (25, 30.001, "positive"),
        (100, 120, "negative"),
        (100, 120.001, "positive"),
        (100, 119, "negative"),
    ],
)
def test_strict_decimal_strengthening_threshold(baseline, future, expected):
    result = label_snapshot(current(baseline), followup([later(frp=future)]))
    assert result.label == expected
    assert result.target_value == int(expected == "positive")
    assert result.unobserved_followup is False


@pytest.mark.parametrize(
    "hours,expected",
    [(0, "negative"), (0.0001, "positive"), (24, "positive"), (24.0001, "negative")],
)
def test_open_start_closed_end(hours, expected):
    result = label_snapshot(current(), followup([later(hours=hours, frp=100)]))
    assert result.label == expected
    assert result.unobserved_followup == (expected == "negative")


@pytest.mark.parametrize(
    "km,expected", [(0, "positive"), (1.99999, "positive"), (2, "positive"), (2.00001, "negative")]
)
def test_closed_haversine_radius(km, expected):
    longitude = math.degrees(km / EARTH_RADIUS_KM)
    result = label_snapshot(current(), followup([later(longitude=longitude, frp=100)]))
    assert result.label == expected


def test_complete_empty_archive_is_unobserved_negative_not_extinction():
    result = label_snapshot(current(), followup())
    assert result.label == "negative" and result.target_value == 0
    assert result.unobserved_followup is True


@pytest.mark.parametrize("frp", [None, 0, 10])
def test_current_missing_baseline_is_unknown_real_zero_is_valid(frp):
    result = label_snapshot(current(frp), followup())
    assert (result.label == "unknown") == (frp is None)
    assert result.unobserved_followup is (None if frp is None else True)


def test_missing_later_frp_prevents_negative_but_does_not_erase_valid_positive():
    missing = later(frp=None)
    assert label_snapshot(current(), followup([missing])).unknown_reasons == [
        "missing_followup_frp"
    ]
    result = label_snapshot(current(), followup([missing, later(i=11, frp=20)]))
    assert result.label == "positive" and result.valid_later_frp_count == 1


@pytest.mark.parametrize("change", ["missing_day", "rejected", "unfinished_day", "failed"])
def test_incomplete_followup_is_unknown_even_with_positive_witness(change):
    evidence = followup([later(frp=100)])
    if change == "missing_day":
        evidence.source_days.pop()
    elif change == "rejected":
        evidence.source_days[-1].rejected_count = 1
    elif change == "unfinished_day":
        evidence.source_days[-1].retrieved_at = T
    else:
        evidence.source_days[-1].status = JobStatus.FAILED
    result = label_snapshot(current(), evidence)
    assert result.label == "unknown" and result.unobserved_followup is None
    assert "incomplete_followup_archive" in result.unknown_reasons


@pytest.mark.parametrize(
    "start,end,blocked", [(-1, 0, False), (0, 1, True), (24, 25, True), (25, 26, False)]
)
def test_outage_half_open_interval_intersects_closed_label_end(start, end, blocked):
    evidence = followup([later(frp=100)])
    evidence.outages = [
        Outage(
            product=Product.NOAA20_SP,
            start=T + timedelta(hours=start),
            end=T + timedelta(hours=end),
            evidence_reference="synthetic-provider-incident",
        )
    ]
    result = label_snapshot(current(), evidence)
    assert ("known_provider_outage" in result.unknown_reasons) == blocked


def test_other_sensor_outage_does_not_block_primary_product():
    evidence = followup()
    evidence.outages = [
        Outage(
            product=Product.NOAA21_SP,
            start=T,
            end=T + timedelta(hours=1),
            evidence_reference="synthetic",
        )
    ]
    assert label_snapshot(current(), evidence).label == "negative"


def test_wide_cluster_and_region_edge_are_unknown():
    item = current()
    item.detail.member_radius_max_km = 13.88
    assert "members_outside_target_radius" in label_snapshot(item, followup()).unknown_reasons
    evidence = followup()
    evidence.bbox.west = -0.001
    assert (
        "target_footprint_outside_source_bbox"
        in label_snapshot(current(), evidence).unknown_reasons
    )


def test_partial_current_frp_is_unknown():
    from feature_factory import observation

    item = current(rows=[observation(1, frp=10), observation(2, frp=None)])
    assert "incomplete_current_frp" in label_snapshot(item, followup()).unknown_reasons


def test_nrt_baseline_cannot_silently_enter_sp_cohort():
    item = current()
    item.products = {Sensor.NOAA20: Product.NOAA20_NRT}
    assert "unsupported_baseline_product" in label_snapshot(item, followup()).unknown_reasons


@pytest.mark.parametrize("change", ["duplicate", "revision", "physical", "sensor", "policy"])
def test_future_revision_integrity_and_sensor_policy(change):
    evidence = followup([later(frp=100)])
    if change == "duplicate":
        evidence.observations.append(deepcopy(evidence.observations[0]))
    elif change == "revision":
        evidence.observations[0].observation.frp_mw = 101
    elif change == "physical":
        evidence.observations[0].physical_key = "0" * 64
    elif change == "sensor":
        row = evidence.observations[0]
        row.observation.product = Product.NOAA21_SP
        row.observation.sensor = Sensor.NOAA21
        row.revision_hash = digest(row.observation.model_dump(mode="json"))
    else:
        evidence.policy.products = {Sensor.NOAA20: Product.NOAA20_NRT}
    with pytest.raises(ValueError):
        label_snapshot(current(), evidence)


def test_future_labels_do_not_mutate_current_features_or_hash():
    item = current()
    before = item.feature.model_dump_json()
    first = label_snapshot(item, followup())
    second = label_snapshot(item, followup([later(frp=100)]))
    assert first.label == "negative" and second.label == "positive"
    assert item.feature.model_dump_json() == before
    assert first.feature_hash == second.feature_hash == item.feature.feature_hash


def test_off_cadence_diagnostic_label_is_excluded_from_training():
    cutoff = T + timedelta(hours=1)
    result = label_snapshot(current(cutoff=cutoff), followup(cutoff=cutoff))
    assert result.label == "negative" and result.dataset_exclusions == ["off_six_hour_cadence"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("radius_km", 3.0),
        ("horizon_hours", 12),
        ("frp_increase_min_mw", 6.0),
        ("frp_increase_fraction", 0.3),
        ("cadence_hours", 1),
    ],
)
def test_changed_prototype_parameters_cannot_reuse_v1(field, value):
    with pytest.raises(ValueError):
        TargetSpec.model_validate(TargetSpec().model_dump() | {field: value})


def test_cadence_and_frozen_design_have_1472_candidate_cutoffs():
    spec = load_target_spec(Path("configs/target-v1.yaml"))
    design = load_design(Path("configs/target-dataset-v1.yaml"))
    assert plan_design(design, spec)["total_candidate_cutoffs"] == 1472
    assert training_cutoffs(T + timedelta(seconds=1), T + timedelta(hours=12), spec) == [
        T + timedelta(hours=6)
    ]
    assert on_cadence(T, spec) and not on_cadence(T + timedelta(microseconds=1), spec)
    assert len(followup_days(T.replace(hour=0))) == 2
    assert spec.sha256 == TargetSpec().sha256
    assert (
        json.loads(Path("docs/target-v1.schema.json").read_bytes())
        == TargetSpec.model_json_schema()
    )


def test_sensitivity_strata_are_reported_without_claiming_model_metrics():
    design = load_design(Path("configs/target-dataset-v1.yaml"))
    rows = [
        label_snapshot(current(), followup()),
        label_snapshot(current(), followup([later(frp=10)])),
    ]
    report = label_report(rows, design)
    assert report["negatives_without_later_detections"] == 1
    assert report["negatives_with_later_detections"] == 1
    assert not report["training_ready"] and not report["cloud_free_coverage_established"]


def test_mismatched_region_and_naive_timestamps_rejected():
    from uuid import uuid4

    evidence = followup()
    evidence.region_id = uuid4()
    with pytest.raises(ValueError, match="context"):
        label_snapshot(current(), evidence)
    with pytest.raises(ValueError):
        FollowupEvidence.model_validate(followup().model_dump() | {"as_of": T.replace(tzinfo=None)})


def test_overflowing_frp_threshold_is_unknown_not_nonfinite_output():
    result = label_snapshot(current(1.7e308), followup())
    assert result.label == "unknown" and result.threshold_mw is None
    assert "invalid_frp_threshold" in result.unknown_reasons


def test_changed_study_bbox_requires_new_design():
    from firewatch.ml.labels import DatasetDesign

    design = load_design(Path("configs/target-dataset-v1.yaml"))
    data = design.model_dump(mode="json")
    data["bbox"]["west"] = -123
    with pytest.raises(ValueError, match="bbox"):
        DatasetDesign.model_validate(data)


def test_complete_source_days_require_explicit_policy_pins():
    evidence = followup()
    evidence.policy = None
    with pytest.raises(ValueError, match="pins"):
        label_snapshot(current(), evidence)
