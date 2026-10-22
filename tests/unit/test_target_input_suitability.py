"""Input exclusions prevent whole-cluster/centroid target spatial mismatch."""

from copy import deepcopy

import pytest
from feature_factory import inputs, observation

from firewatch.processing.features import build_features
from firewatch.processing.suitability import centroid_target_exclusions


@pytest.mark.parametrize(
    "radius,excluded", [(0, False), (2, False), (2.000001, True), (13.88, True)]
)
def test_closed_radius_policy(radius, excluded):
    args = inputs([observation(1)])
    feature = build_features(*args)[0]
    detail = deepcopy(args[0].snapshots[0])
    detail.member_radius_max_km = radius
    assert (
        "members_outside_target_radius" in centroid_target_exclusions(detail, feature)
    ) == excluded


@pytest.mark.parametrize("frp", [None, 0.0, 10.0])
def test_missing_frp_excluded_but_real_zero_is_eligible(frp):
    args = inputs([observation(1, frp=frp)])
    feature = build_features(*args)[0]
    reasons = centroid_target_exclusions(args[0].snapshots[0], feature)
    assert ("missing_current_frp" in reasons) == (frp is None)
    assert ("incomplete_current_frp" in reasons) == (frp is None)


def test_partial_frp_baseline_is_excluded():
    args = inputs([observation(1, frp=None), observation(2, frp=10)])
    feature = build_features(*args)[0]
    assert centroid_target_exclusions(args[0].snapshots[0], feature) == ["incomplete_current_frp"]


def test_different_snapshot_cannot_supply_target_diagnostics():
    from uuid import uuid4

    args = inputs([observation(1)])
    feature = build_features(*args)[0]
    detail = deepcopy(args[0].snapshots[0])
    detail.snapshot.snapshot_id = uuid4()
    with pytest.raises(ValueError, match="context"):
        centroid_target_exclusions(detail, feature)
