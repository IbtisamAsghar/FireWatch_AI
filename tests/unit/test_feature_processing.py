"""Feature definitions, model whitelist, units, missingness and temporal boundaries."""

import math
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from cluster_factory import T
from feature_factory import inputs, observation
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.processing.clusters import EARTH_RADIUS_KM
from firewatch.processing.features import (
    MISSING_NAMES,
    MODEL_INPUT_NAMES,
    UNITS,
    AssetPin,
    FeatureSpec,
    FeatureValues,
    build_features,
    load_feature_spec,
    model_inputs,
    validate_feature_set,
)
from firewatch.schemas.common import Confidence, DayNight, RunMode
from firewatch.schemas.contracts import WeatherSeries
from firewatch.sources.firms_normalize import digest


def build(bundle=None, config=None):
    return build_features(*(bundle or inputs()), config=config)


def test_exact_feature_values_and_units():
    rows = [
        observation(1, frp=5, brightness=320, daynight=DayNight.DAY),
        observation(
            2, hours=3, frp=10, brightness=340, confidence=Confidence.LOW, daynight=DayNight.NIGHT
        ),
        observation(3, hours=20),
    ]
    feature = build(inputs(rows))[0]
    assert feature.values == dict(
        frp_max_mw=10.0,
        frp_sum_mw=15.0,
        brightness_max_k=340.0,
        detections_6h=2,
        detections_12h=2,
        detections_24h=3,
        hours_since_first_seen_24h=20.0,
        confidence_high_fraction=0.5,
        day_fraction=0.5,
        temperature_c=1.0,
        humidity_pct=1.0,
        wind_ms=1.0,
        gust_ms=1.0,
        precipitation_24h_mm=24.0,
        temperature_departure_c=-9.0,
        rainfall_deficit_24h_mm=0.0,
        weather_age_hours=0.0,
    )
    assert feature.units == UNITS and set(feature.missing_flags) == set(MISSING_NAMES)
    assert not any(feature.missing_flags.values())


def test_model_input_whitelist_and_hash_integrity():
    feature = build()[0]
    model = model_inputs(feature)
    assert tuple(model) == MODEL_INPUT_NAMES and len(model) == 31
    assert all(isinstance(v, (int, float, bool)) or v is None for v in model.values())
    assert not set(model) & {
        "latitude",
        "longitude",
        "run_id",
        "snapshot_id",
        "as_of",
        "label",
        "priority",
        "anchored",
    }
    assert feature.feature_hash == digest(feature.model_dump(mode="json", exclude={"feature_hash"}))


@pytest.mark.parametrize(
    "field",
    ["latitude", "longitude", "event_id", "label", "risk_label", "future_sensor_count", "anchored"],
)
def test_extra_inputs_are_rejected(field):
    feature = build()[0]
    feature.values[field] = 1
    feature.feature_hash = digest(feature.model_dump(mode="json", exclude={"feature_hash"}))
    with pytest.raises(ValueError):
        model_inputs(feature)


@pytest.mark.parametrize("field", ["temperature_c", "wind_ms", "brightness_max_k"])
def test_nonfinite_and_boolean_values_refused(field):
    for value in [float("nan"), float("inf"), True]:
        values = dict(build()[0].values)
        values[field] = value
        with pytest.raises(ValueError):
            FeatureValues.model_validate(values)


def test_missing_confidence_and_daynight_are_not_negative_classes():
    rows = [observation(1, confidence=Confidence.UNKNOWN, daynight=None)]
    result = build(inputs(rows))[0]
    assert (
        result.values["confidence_high_fraction"] is None and result.values["day_fraction"] is None
    )
    assert result.missing_flags["confidence_high_fraction"] and result.missing_flags["day_fraction"]
    assert "daynight_missing_members" in result.lineage["quality_flags"]
    rows.append(observation(2, confidence=Confidence.HIGH, daynight=DayNight.DAY))
    result = build(inputs(rows))[0]
    assert result.values["confidence_high_fraction"] == 1 and result.values["day_fraction"] is None


def test_missing_frp_brightness_and_genuine_zero():
    result = build(inputs([observation(1, frp=None, brightness=None)]))[0]
    assert result.values["frp_sum_mw"] is None and result.values["brightness_max_k"] is None
    assert result.missing_flags["frp_max_mw"]
    result = build(inputs([observation(1, frp=0), observation(2, frp=None)]))[0]
    assert result.values["frp_sum_mw"] == 0 and not result.missing_flags["frp_sum_mw"]
    assert "frp_aggregates_partial" in result.lineage["quality_flags"]


def test_missing_baseline_and_weather_are_flagged():
    bundle = inputs(baseline=False)
    feature = build(bundle)[0]
    assert feature.values["temperature_departure_c"] is None
    assert "climate_baseline_missing" in feature.lineage["quality_flags"]
    bundle[2][0].weather = WeatherSeries(records=[])
    bundle[2][0].weather_assets = []
    feature = build(bundle)[0]
    assert feature.values["temperature_c"] is None and feature.values["weather_age_hours"] is None
    assert feature.values["precipitation_24h_mm"] is None
    assert feature.values["detections_6h"] == 1


def test_partial_weather_does_not_zero_fill_rainfall():
    bundle = inputs()
    bundle[2][0].weather.records.pop(5)
    result = build(bundle)[0]
    assert result.values["temperature_c"] == 1 and result.values["precipitation_24h_mm"] is None
    assert result.values["rainfall_deficit_24h_mm"] is None
    assert "precipitation_24h_incomplete" in result.lineage["quality_flags"]


def test_deficit_and_nonhour_timestamp():
    bundle = inputs(cutoff=T + timedelta(minutes=30))
    for row in bundle[2][0].weather.records:
        row.precipitation_mm = 0
    feature = build(bundle)[0]
    assert feature.values["weather_age_hours"] == 0.5
    assert feature.values["hours_since_first_seen_24h"] == 20.5
    assert feature.values["rainfall_deficit_24h_mm"] == 2
    assert feature.lineage["weather"]["precipitation_interval_end"] == T.isoformat()


def test_future_records_and_exact_24h_boundary_do_not_change_features():
    bundle = inputs()
    expected = build(bundle)
    bundle[1].extend(
        [
            observation(50, acquired=T + timedelta(microseconds=1), frp=9999),
            observation(51, hours=24, frp=9999),
        ]
    )
    future = deepcopy(bundle[2][0].weather.records[-1])
    future.valid_at += timedelta(hours=1)
    future.temperature_c = 100
    future.asset_id = uuid4()
    bundle[2][0].weather.records.append(future)
    bundle[2][0].weather_assets.append(AssetPin(asset_id=future.asset_id, sha256="d" * 64))
    assert build(bundle) == expected


def test_live_and_replay_model_values_share_one_builder():
    replay = build(inputs(mode=RunMode.REPLAY))[0]
    live = build(inputs(mode=RunMode.LIVE))[0]
    assert model_inputs(replay) == model_inputs(live)
    assert replay.mode != live.mode and replay.feature_hash != live.feature_hash


def test_day_age_is_not_full_incident_age_when_centroid_is_empty():
    rows = []
    for i in range(20):
        angle = 2 * math.pi * i / 20
        rows.append(
            observation(
                i,
                latitude=math.degrees(4 / EARTH_RADIUS_KM) * math.cos(angle),
                longitude=math.degrees(4 / EARTH_RADIUS_KM) * math.sin(angle),
            )
        )
    result = build(inputs(rows))[0]
    assert (
        result.values["detections_24h"] == 0 and result.values["hours_since_first_seen_24h"] is None
    )
    assert result.missing_flags["hours_since_first_seen_24h"]


@pytest.mark.parametrize(
    "damage",
    [
        "weather_unit",
        "weather_cell",
        "weather_product",
        "baseline_year",
        "asset",
        "snapshot",
        "revision",
    ],
)
def test_input_mismatches_refused(damage):
    bundle = inputs()
    if damage == "weather_unit":
        bundle[2][0].weather.records[-1].units["wind_ms"] = "km/h"
    if damage == "weather_cell":
        bundle[2][0].weather.records[-1].cell_key = "other"
    if damage == "weather_product":
        bundle[2][0].weather.records[-1].product = "live"
    if damage == "baseline_year":
        bundle[2][0].baseline.reference_end_year = 2024
    if damage == "asset":
        bundle[2][0].weather_assets = []
    if damage == "snapshot":
        bundle[0].snapshots[0].snapshot.frp_sum_mw = 100
    if damage == "revision":
        bundle[1][0].observation.frp_mw = 100
    with pytest.raises(ValueError):
        build(bundle)


@pytest.mark.parametrize("damage", ["value", "flag", "unit", "hash"])
def test_feature_tampering_refused(damage):
    feature = build()[0]
    if damage == "value":
        feature.values["temperature_c"] = 100
    if damage == "flag":
        feature.missing_flags["temperature_c"] = True
    if damage == "unit":
        feature.units["wind_ms"] = "km/h"
    if damage == "hash":
        feature.feature_hash = "f" * 64
    with pytest.raises(ValueError):
        validate_feature_set(feature)


def test_ordering_and_config_validation():
    bundle = inputs([observation(1), observation(2, hours=3), observation(3, hours=20)])
    expected = build(bundle)
    bundle[1].reverse()
    bundle[2][0].weather.records.reverse()
    assert build(bundle) == expected
    assert load_feature_spec(Path("configs/features-v1.yaml")) == FeatureSpec()
    assert build_features(*inputs([])) == []
    with pytest.raises(ValueError):
        FeatureSpec(weather_max_age_hours=0)
    with pytest.raises(ValueError):
        FeatureSpec(replay_weather_product="reanalysis")
    result = CliRunner().invoke(app, ["build-features", "--run-id", "bad"])
    assert result.exit_code == 1 and "Feature build failed" in result.output


def test_staleness_is_flagged_without_inventing_recent_weather():
    bundle = inputs()
    bundle[2][0].weather.records = bundle[2][0].weather.records[:-4]
    feature = build(bundle)[0]
    assert feature.values["weather_age_hours"] == 4
    assert feature.values["temperature_c"] == 1
    assert "weather_stale" in feature.lineage["quality_flags"]
    assert feature.values["precipitation_24h_mm"] is None


def test_known_night_and_nonhigh_confidence_are_valid_zero_fractions():
    feature = build(
        inputs([observation(1, confidence=Confidence.NOMINAL, daynight=DayNight.NIGHT)])
    )[0]
    assert feature.values["confidence_high_fraction"] == 0 and feature.values["day_fraction"] == 0
    assert (
        not feature.missing_flags["day_fraction"]
        and not feature.missing_flags["confidence_high_fraction"]
    )


def test_changed_spatial_definition_requires_a_new_feature_schema():
    from firewatch.processing.clusters import ClusteringSpec, build_snapshots

    bundle = inputs()
    bundle = (
        build_snapshots(
            bundle[0].run_id,
            bundle[0].mode,
            bundle[0].as_of,
            bundle[1],
            ClusteringSpec(radius_km=3, algorithm_version="dbscan-haversine-3km-v2"),
        ),
        *bundle[1:],
    )
    with pytest.raises(ValueError, match="2 km"):
        build(bundle)


def test_output_order_is_deterministic_and_snapshot_counts_are_checked():
    rows = [observation(1), observation(2, longitude=0.1)]
    bundle = inputs(rows)
    expected = build(bundle)
    bundle[0].snapshots.reverse()
    bundle[2].reverse()
    assert build(bundle) == expected
    bundle[0].history_count = 999
    with pytest.raises(ValueError, match="history count"):
        build(bundle)


def test_climate_coverage_definition_and_units_must_match_feature_configuration():
    for key, value in [
        ("min_valid_fraction", 0.5),
        ("earliest_snapshot", "2020-01-01T00:00:00Z"),
        ("units", {"temperature_mean_c": "K", "precipitation_mean_mm": "mm/month"}),
    ]:
        bundle = inputs()
        bundle[2][0].baseline.statistics[key] = value
        with pytest.raises(ValueError, match="coverage definition or units"):
            build(bundle)
