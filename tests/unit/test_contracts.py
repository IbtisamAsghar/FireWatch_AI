"""DTO boundary checks independent of a database or provider access."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from firewatch.schemas.common import Confidence, Priority, Product, RunMode, ScoreKind, Sensor
from firewatch.schemas.contracts import BoundingBox, DetectionInput, RegionConfig, ScoreResult


def detection(**values):
    fields = dict(
        product=Product.NOAA20_NRT,
        sensor=Sensor.NOAA20,
        latitude=34,
        longitude=71,
        acquired_at="2025-06-10T12:00:00Z",
        dedup_key="a" * 64,
    )
    return DetectionInput(**(fields | values))


@pytest.mark.parametrize(
    "field,value",
    [
        ("latitude", 91),
        ("longitude", -181),
        ("frp_mw", -1),
        ("brightness_k", 0),
        ("frp_mw", float("nan")),
        ("frp_mw", float("inf")),
        ("product", "VIIRS_SNPP_NRT"),
        ("sensor", Sensor.NOAA21),
        ("confidence", "90%"),
        ("acquired_at", "2025-06-10T12:00:00"),
        ("acquired_at", 1750000000),
    ],
)
def test_invalid_detection_contract(field, value):
    with pytest.raises(ValidationError):
        detection(**{field: value})


def test_aware_times_normalize_to_utc_and_serialize_z():
    record = detection(acquired_at="2025-06-10T17:00:00+05:00")
    assert record.acquired_at == datetime(2025, 6, 10, 12, tzinfo=UTC)
    assert record.model_dump(mode="json")["acquired_at"] == "2025-06-10T12:00:00Z"


def test_raw_invalid_provider_values_remain_available_without_zero_fill():
    record = detection(
        raw_values={"frp": "bad", "confidence": "unrecognized"},
        confidence_raw="unrecognized",
        quality_flags=["invalid_frp"],
    )
    assert record.frp_mw is None and record.confidence == Confidence.UNKNOWN
    assert record.raw_values["frp"] == "bad" and record.confidence_raw == "unrecognized"


@pytest.mark.parametrize(
    "bbox", [dict(west=72, east=70, south=33, north=36), dict(west=70, east=74, south=36, north=36)]
)
def test_bbox_order(bbox):
    with pytest.raises(ValidationError):
        BoundingBox(**bbox)


def test_boundary_coordinates_and_unknown_fields():
    assert BoundingBox(west=-180, south=-90, east=180, north=90).east == 180
    with pytest.raises(ValidationError):
        detection(future_label=True)


def test_region_config_duplicates_and_empty_rejected():
    item = {"name": "demo-region", "bbox": {"west": 70, "south": 33, "east": 74, "north": 36}}
    for entries in ([], [item, item]):
        with pytest.raises(ValidationError):
            RegionConfig(regions=entries)


def test_baseline_and_uncalibrated_scores_have_null_probability():
    values = dict(
        run_id=uuid4(),
        snapshot_id=uuid4(),
        mode=RunMode.REPLAY,
        as_of="2025-06-10T12:00:00Z",
        feature_version="features-v1",
        scorer_version="baseline-v1",
        score_kind=ScoreKind.BASELINE,
        priority_score=0.7,
        priority=Priority.HIGH,
        reasons=[],
        scored_at="2025-06-11T12:00:00Z",
    )
    assert ScoreResult(**values).escalation_probability is None
    with pytest.raises(ValidationError):
        ScoreResult(**values, escalation_probability=0.7)
    with pytest.raises(ValidationError):
        ScoreResult(**(values | {"score_kind": ScoreKind.MODEL}))


@pytest.mark.parametrize(
    "metadata",
    [
        {"api_key": "test-secret-value"},
        {"nested": {"authorization": "test-secret-value"}},
        {
            "url": "https://firms.modaps.eosdis.nasa.gov/api/area/csv/test-secret-value/product/area/1"
        },
        {"url": "https://example.com?token=test-secret-value"},
    ],
)
def test_source_metadata_cannot_persist_credentials(metadata):
    from firewatch.schemas.contracts import SourceAssetInput

    with pytest.raises(ValidationError) as error:
        SourceAssetInput(
            provider="firms",
            product="VIIRS_NOAA20_NRT",
            request_metadata=metadata,
            retrieved_at="2025-06-10T12:00:00Z",
            storage_path="sample.csv",
            content_hash="a" * 64,
            format="csv",
            processing_version="v1",
        )
    assert "test-secret-value" not in str(error.value)


def test_contract_schema_version_is_not_arbitrary():
    from firewatch.schemas.contracts import FeatureSet

    with pytest.raises(ValidationError):
        FeatureSet(
            schema_version="unknown",
            run_id=uuid4(),
            mode="live",
            as_of="2025-06-10T12:00:00Z",
            feature_version="v1",
            snapshot_id=uuid4(),
            values={},
            missing_flags={},
            units={},
            lineage={},
            feature_hash="a" * 64,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("humidity_pct", 101),
        ("wind_ms", -1),
        ("gust_ms", float("inf")),
        ("precipitation_mm", -1),
        ("temperature_c", -274),
        ("valid_at", "2025-06-10T12:00:00"),
    ],
)
def test_weather_ranges_and_utc_contract(field, value):
    from firewatch.schemas.contracts import WeatherRecordInput

    values = dict(
        cell_key="cell-v1",
        latitude=34,
        longitude=71,
        requested_latitude=34,
        requested_longitude=71,
        model="model-v1",
        product="historical-forecast",
        source_version="v1",
        valid_at="2025-06-10T12:00:00Z",
        retrieved_at="2025-06-10T12:00:00Z",
        units={},
        asset_id=uuid4(),
    )
    with pytest.raises(ValidationError):
        WeatherRecordInput(**(values | {field: value}))


def test_alert_dto_checks_the_exact_payload_digest():
    from firewatch.schemas.contracts import AlertEvidence

    with pytest.raises(ValidationError):
        AlertEvidence(
            run_id=uuid4(),
            snapshot_id=uuid4(),
            score_id=uuid4(),
            alert_id=uuid4(),
            mode="replay",
            as_of="2025-06-10T12:00:00Z",
            feature_version="v1",
            evidence_schema_version="v1",
            canonical_payload=b"changed",
            original_hash="a" * 64,
            created_at="2025-06-10T12:00:00Z",
        )


def test_completed_run_result_requires_complete_stage_metadata():
    from firewatch.schemas.contracts import RunResult

    with pytest.raises(ValidationError):
        RunResult(
            run_id=uuid4(),
            region_id=uuid4(),
            mode="live",
            as_of="2025-06-10T12:00:00Z",
            feature_version="v1",
            status="completed",
            completeness="complete",
            stages={},
            completed_at="2025-06-10T12:00:00Z",
        )
