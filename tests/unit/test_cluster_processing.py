"""Deterministic spherical clustering and past-only activity regression tests."""

import math
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cluster_factory import RUN, T, selected
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.processing.clusters import (
    EARTH_RADIUS_KM,
    ClusteringSpec,
    build_snapshots,
    distance_km,
    load_clustering_spec,
)
from firewatch.schemas.common import Confidence, RunMode, Sensor


def build(rows, config=None, run_id=RUN):
    return build_snapshots(run_id, RunMode.REPLAY, T, rows, config)


def east(km):
    return math.degrees(km / EARTH_RADIUS_KM)


def test_exact_temporal_boundaries():
    micro = timedelta(microseconds=1)
    times = [
        T,
        T - timedelta(hours=6) + micro,
        T - timedelta(hours=6),
        T - timedelta(hours=12) + micro,
        T - timedelta(hours=12),
        T - timedelta(hours=24) + micro,
        T - timedelta(hours=24),
        T + micro,
    ]
    batch = build([selected(i, acquired=t) for i, t in enumerate(times)])
    assert batch.history_count == 6 and batch.candidate_count == 2
    item = batch.snapshots[0]
    assert len(item.snapshot.member_ids) == 2 and item.snapshot.latest_detection_at == T
    assert item.activity_counts == {"detections_6h": 2, "detections_12h": 4, "detections_24h": 6}


@pytest.mark.parametrize("km,clusters", [(0, 1), (1.999999, 1), (2.000001, 2), (10, 2)])
def test_distance_threshold(km, clusters):
    batch = build([selected(1), selected(2, longitude=east(km))])
    assert len(batch.snapshots) == clusters
    assert distance_km(0, 0, 0, east(km)) == pytest.approx(km, abs=1e-9)


def test_two_km_closed_boundary():
    assert len(build([selected(1), selected(2, longitude=east(2))]).snapshots) == 1


def test_singleton_and_empty_not_noise():
    singleton = build([selected(1)])
    assert len(singleton.snapshots) == 1
    assert singleton.snapshots[0].snapshot.member_ids == [selected(1).detection_id]
    assert build([]).snapshots == []
    assert build([selected(1, hours=7)]).candidate_count == 0


def test_input_permutation_and_run_identity():
    rows = [selected(i, longitude=east(i)) for i in range(6)]
    first = build(rows)
    assert first == build(list(reversed(rows)))
    other = build(rows, run_id=uuid4())
    assert first.snapshots[0].snapshot.snapshot_id != other.snapshots[0].snapshot.snapshot_id


def test_chaining_centroid_and_independent_activity_radius():
    rows = [selected(i, longitude=east(i * 1.9)) for i in range(4)]
    item = build(rows).snapshots[0]
    assert len(item.snapshot.member_ids) == 4
    assert item.member_radius_max_km == pytest.approx(2.85, abs=1e-7)
    assert item.activity_counts["detections_6h"] == 2
    assert "members_outside_centroid_radius" in item.quality_flags


def test_history_is_near_centroid_not_only_members():
    item = build(
        [
            selected(1),
            selected(2, hours=10),
            selected(3, hours=20),
            selected(4, longitude=east(2.1), hours=20),
        ]
    ).snapshots[0]
    assert item.activity_counts == {"detections_6h": 1, "detections_12h": 2, "detections_24h": 3}


def test_antimeridian_and_high_latitude_spherical_distances():
    item = build([selected(1, longitude=179.999), selected(2, longitude=-179.999)]).snapshots[0]
    assert abs(abs(item.snapshot.longitude) - 180) < 1e-8
    assert len(item.snapshot.member_ids) == 2
    high = build([selected(3, latitude=89, longitude=0), selected(4, latitude=89, longitude=0.5)])
    assert len(high.snapshots) == 1
    assert len(build([selected(5, longitude=0), selected(6, longitude=0.5)]).snapshots) == 2


def test_sensor_confidence_and_frp_aggregates():
    item = build(
        [
            selected(1, frp=0, confidence=Confidence.LOW),
            selected(2, frp=20, sensor=Sensor.NOAA21, confidence=Confidence.UNKNOWN),
        ]
    ).snapshots[0]
    assert item.snapshot.frp_sum_mw == 20 and item.snapshot.frp_max_mw == 20
    assert item.sensor_counts == {"NOAA-20": 1, "NOAA-21": 1}
    assert item.confidence_counts == {"low": 1, "nominal": 0, "high": 0, "unknown": 1}


@pytest.mark.parametrize(
    "frps,expected,missing", [([None, None], None, 2), ([None, 0], 0, 1), ([2, 3], 5, 0)]
)
def test_null_frp_never_becomes_zero(frps, expected, missing):
    item = build([selected(i, frp=f) for i, f in enumerate(frps)]).snapshots[0]
    assert item.snapshot.frp_sum_mw == expected and item.frp_missing_members == missing
    assert ("frp_missing_members" in item.quality_flags) == bool(missing)


def test_future_observations_do_not_change_hashes_or_results():
    past = [selected(1)]
    assert build(past) == build(past + [selected(2, acquired=T + timedelta(seconds=1), frp=9999)])
    assert build(past) == build(past + [selected(3, hours=24)])


def test_duplicate_and_tampered_revisions_refused():
    first = selected(1)
    with pytest.raises(ValueError, match="physical identity"):
        build([first, first])
    changed = deepcopy(first)
    changed.observation.frp_mw = 100
    with pytest.raises(ValueError, match="hash"):
        build([changed])
    duplicate = deepcopy(first)
    duplicate.detection_id = uuid4()
    with pytest.raises(ValueError, match="physical identity"):
        build([first, duplicate])


@pytest.mark.parametrize(
    "overrides",
    [
        dict(radius_km=3),
        dict(radius_km=0),
        dict(earth_radius_km=6371),
        dict(min_samples=2),
        dict(candidate_hours=12),
        dict(activity_hours=(1, 2, 3)),
        dict(radius_km=float("nan")),
        dict(max_candidates=0),
    ],
)
def test_configuration_bounds(overrides):
    with pytest.raises(ValueError):
        ClusteringSpec(**overrides)


def test_versioned_radius_and_resource_limits():
    config = ClusteringSpec(algorithm_version="dbscan-haversine-3km-v2", radius_km=3)
    assert len(build([selected(1), selected(2, longitude=east(2.5))], config).snapshots) == 1
    for config in [
        ClusteringSpec(max_history=1),
        ClusteringSpec(max_candidates=1),
        ClusteringSpec(max_neighbor_links=3),
    ]:
        with pytest.raises(ValueError):
            build([selected(1), selected(2)], config)


def test_naive_cutoff_and_bad_coordinate_refused():
    with pytest.raises(ValueError):
        build_snapshots(RUN, RunMode.REPLAY, T.replace(tzinfo=None), [])
    with pytest.raises(ValueError):
        distance_km(91, 0, 0, 0)


def test_shared_config_and_cli_input_validation():
    config = load_clustering_spec(Path("configs/features-v1.yaml"))
    assert config == ClusteringSpec()
    result = CliRunner().invoke(
        app, ["build-snapshots", "--region", "northern-california", "--as-of", "bad"]
    )
    assert result.exit_code == 1 and "Snapshot stage failed" in result.output


def test_single_product_policy_and_physical_identity():
    from firewatch.schemas.common import Product

    rows = [selected(1, product=Product.NOAA20_NRT), selected(2, product=Product.NOAA20_SP)]
    with pytest.raises(ValueError, match="one product"):
        build(rows)
    changed = selected(1)
    changed.physical_key = "f" * 64
    with pytest.raises(ValueError, match="physical identity"):
        build([changed])


def test_live_and_replay_use_same_spatial_numeric_definitions():
    rows = [selected(1), selected(2, longitude=east(1.5)), selected(3, hours=18)]
    replay = build(rows)
    live = build_snapshots(RUN, RunMode.LIVE, T, rows)
    assert live.snapshots[0].activity_counts == replay.snapshots[0].activity_counts
    for name in ["latitude", "longitude", "frp_max_mw", "frp_sum_mw", "member_ids"]:
        assert getattr(live.snapshots[0].snapshot, name) == getattr(
            replay.snapshots[0].snapshot, name
        )


def test_centroid_activity_closed_two_km_boundary():
    item = build(
        [
            selected(1),
            selected(2, longitude=east(2), hours=8),
            selected(3, longitude=east(2.000001), hours=8),
        ]
    ).snapshots[0]
    assert item.activity_counts["detections_12h"] == 2
