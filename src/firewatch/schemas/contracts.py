"""contracts-v1: typed handoffs; no provider, pipeline or ML implementation implied."""

import hashlib
import re
from datetime import date
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from firewatch.schemas.common import (
    DTO,
    Completeness,
    Confidence,
    DayNight,
    Digest,
    Latitude,
    Longitude,
    Nonnegative,
    Priority,
    Product,
    Provider,
    RunMode,
    RunStatus,
    ScoreKind,
    Sensor,
    Stage,
    StageStatus,
    UTCDateTime,
    Version,
)


class BoundingBox(DTO):
    west: Longitude
    south: Latitude
    east: Longitude
    north: Latitude

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.west >= self.east or self.south >= self.north:
            raise ValueError("Bounding box requires west < east and south < north")
        return self


class SourceSampleWindow(DTO):
    name: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)]
    start_date: date
    days: int = Field(ge=1, le=5)
    incident_reference: Annotated[
        str, Field(pattern=r"^https://www\.fire\.ca\.gov/incidents/[0-9/]+[a-z-]+/?$")
    ]


class RegionSourceAccess(DTO):
    """Optional research metadata; never enables a region or asserts unverified coverage."""

    schema_version: Literal["region-source-access-v1"] = "region-source-access-v1"
    status: Literal["candidate", "probed", "reviewed"] = "candidate"
    selection_reason: Annotated[str, Field(min_length=1, max_length=1000)]
    live_product: Product = Product.NOAA20_NRT
    historical_product: Product = Product.NOAA20_SP
    comparison_products: list[Product] = Field(default_factory=list, max_length=2)
    season_months: Annotated[
        list[Annotated[int, Field(ge=1, le=12)]], Field(min_length=1, max_length=12)
    ]
    requested_months: Annotated[
        list[Annotated[str, Field(pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$")]],
        Field(min_length=1, max_length=120),
    ]
    available_months: dict[
        Product, list[Annotated[str, Field(pattern=r"^20[0-9]{2}-(?:0[1-9]|1[0-2])$")]]
    ] = Field(default_factory=dict)
    sample_windows: Annotated[list[SourceSampleWindow], Field(min_length=2, max_length=4)]
    archive_status: Literal["not_requested", "requested", "delivered"] = "not_requested"
    weather_model: Literal["gfs_seamless"] = "gfs_seamless"
    weather_product: Literal["historical_forecast"] = "historical_forecast"
    evidence_report: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.live_product not in {Product.NOAA20_NRT, Product.NOAA21_NRT}:
            raise ValueError("Live study product must be NRT")
        if self.historical_product.value != self.live_product.value.replace("_NRT", "_SP"):
            raise ValueError("Historical study product must be SP from the same sensor")
        if len(set(self.season_months)) != len(self.season_months) or len(
            set(self.requested_months)
        ) != len(self.requested_months):
            raise ValueError("Study months must be unique")
        starts = [window.start_date for window in self.sample_windows]
        if len(set(starts)) != len(starts):
            raise ValueError("Sample windows must be distinct")
        if any(start.strftime("%Y-%m") not in self.requested_months for start in starts):
            raise ValueError("Sample windows must belong to requested months")
        if self.status != "candidate" and not self.evidence_report:
            raise ValueError("Probed/reviewed study requires an evidence report")
        return self


class RegionDefinition(DTO):
    name: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)]
    bbox: BoundingBox
    enabled: bool = False
    description: Annotated[str, Field(max_length=500)] = ""
    source_access: RegionSourceAccess | None = None


class RegionConfig(DTO):
    schema_version: str = "regions-v1"
    regions: Annotated[list[RegionDefinition], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_names(self) -> Self:
        if self.schema_version != "regions-v1":
            raise ValueError("Unsupported region configuration version")
        if len({region.name for region in self.regions}) != len(self.regions):
            raise ValueError("Region names must be unique")
        return self


class SourceAssetInput(DTO):
    provider: Provider
    product: Version
    request_metadata: dict[str, JsonValue] = Field(default_factory=dict)
    retrieved_at: UTCDateTime
    storage_path: Annotated[str, Field(min_length=1, max_length=1000)]
    content_hash: Digest
    format: Version
    processing_version: Version

    @field_validator("request_metadata")
    @classmethod
    def sanitized_metadata(cls, metadata: dict[str, JsonValue]) -> dict[str, JsonValue]:
        forbidden = {
            "map_key",
            "mapkey",
            "api_key",
            "apikey",
            "authorization",
            "password",
            "token",
            "access_token",
            "private_key",
            "secret",
            "database_url",
        }

        def inspect(value: JsonValue) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.lower().replace("-", "_") in forbidden:
                        raise ValueError("Request metadata must not contain credential fields")
                    inspect(child)
            elif isinstance(value, list):
                for child in value:
                    inspect(child)
            elif isinstance(value, str):
                if re.search(r"/api/area/csv/(?!\{MAP_KEY\}|\[REDACTED\])", value):
                    raise ValueError("FIRMS credential path must be redacted before persistence")
                if re.search(r"(?i)(?:api_key|map_key|token|password|secret)=", value):
                    raise ValueError("Credential-bearing request URLs must not be persisted")

        inspect(metadata)
        return metadata


class DetectionInput(DTO):
    product: Product
    sensor: Sensor
    latitude: Latitude
    longitude: Longitude
    acquired_at: UTCDateTime
    frp_mw: Nonnegative | None = None
    brightness_k: Annotated[float, Field(gt=0)] | None = None
    confidence_raw: str | None = None
    confidence: Confidence = Confidence.UNKNOWN
    daynight: DayNight | None = None
    scan: Nonnegative | None = None
    track: Nonnegative | None = None
    raw_values: dict[str, JsonValue] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    dedup_key: Digest

    @model_validator(mode="after")
    def sensor_product_match(self) -> Self:
        expected = Sensor.NOAA20 if "NOAA20" in self.product else Sensor.NOAA21
        if self.sensor != expected:
            raise ValueError("Sensor does not match the selected FIRMS product")
        return self


class DetectionBatch(DTO):
    schema_version: Literal["contracts-v1"] = "contracts-v1"
    asset_id: UUID
    batch_id: UUID
    region_id: UUID
    product: Product
    window_start: UTCDateTime
    window_end: UTCDateTime
    detections: list[DetectionInput]
    rejected_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def window_and_products(self) -> Self:
        if self.window_start >= self.window_end:
            raise ValueError("Ingestion start must precede end")
        if any(
            d.product != self.product or not self.window_start <= d.acquired_at <= self.window_end
            for d in self.detections
        ):
            raise ValueError("Detections must match the batch product and requested UTC window")
        return self


class WeatherRecordInput(DTO):
    cell_key: Version
    latitude: Latitude
    longitude: Longitude
    requested_latitude: Latitude
    requested_longitude: Longitude
    provider: Provider = Provider.OPEN_METEO
    model: Version
    product: Version
    source_version: Version
    valid_at: UTCDateTime
    retrieved_at: UTCDateTime
    temperature_c: Annotated[float, Field(ge=-273.15)] | None = None
    humidity_pct: Annotated[float, Field(ge=0, le=100)] | None = None
    wind_ms: Nonnegative | None = None
    gust_ms: Nonnegative | None = None
    precipitation_mm: Nonnegative | None = None
    units: dict[str, str]
    quality_flags: list[str] = Field(default_factory=list)
    asset_id: UUID


class WeatherSeries(DTO):
    schema_version: Literal["contracts-v1"] = "contracts-v1"
    records: list[WeatherRecordInput]
    quality_flags: list[str] = Field(default_factory=list)


class ClimateBaseline(DTO):
    schema_version: Literal["contracts-v1"] = "contracts-v1"
    cell_key: Version
    latitude: Latitude
    longitude: Longitude
    source: Provider = Provider.POWER
    reference_start_year: int = Field(ge=1900, le=2100)
    reference_end_year: int = Field(ge=1900, le=2100)
    month: int = Field(ge=1, le=12)
    temperature_mean_c: Annotated[float, Field(ge=-273.15)] | None = None
    precipitation_mean_mm: Nonnegative | None = None
    sample_counts: dict[str, Annotated[int, Field(ge=0)]]
    statistics: dict[str, JsonValue]
    version: Version
    asset_id: UUID

    @model_validator(mode="after")
    def reference_order(self) -> Self:
        if self.reference_start_year > self.reference_end_year:
            raise ValueError("Climate reference years are reversed")
        return self


class RunContext(DTO):
    schema_version: Literal["contracts-v1"] = "contracts-v1"
    run_id: UUID
    mode: RunMode
    as_of: UTCDateTime
    feature_version: Version


class ClusterSnapshot(RunContext):
    snapshot_id: UUID
    latitude: Latitude
    longitude: Longitude
    member_ids: Annotated[list[UUID], Field(min_length=1)]
    frp_max_mw: Nonnegative | None = None
    frp_sum_mw: Nonnegative | None = None
    latest_detection_at: UTCDateTime
    algorithm_version: Version

    @model_validator(mode="after")
    def past_members(self) -> Self:
        if self.latest_detection_at > self.as_of or len(set(self.member_ids)) != len(
            self.member_ids
        ):
            raise ValueError("Snapshot members must be unique and observed at or before cutoff")
        return self


class FeatureSet(RunContext):
    snapshot_id: UUID
    values: dict[str, JsonValue]
    missing_flags: dict[str, bool]
    units: dict[str, str]
    lineage: dict[str, JsonValue]
    feature_hash: Digest


class ScoreResult(RunContext):
    snapshot_id: UUID
    scorer_version: Version
    model_id: UUID | None = None
    score_kind: ScoreKind
    priority_score: Nonnegative
    escalation_probability: Annotated[float, Field(ge=0, le=1)] | None = None
    priority: Priority
    reasons: list[str]
    explanation: dict[str, JsonValue] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    scored_at: UTCDateTime

    @model_validator(mode="after")
    def probability_claim(self) -> Self:
        calibrated = self.score_kind == ScoreKind.PROBABILITY
        if calibrated != (self.escalation_probability is not None):
            raise ValueError("Only calibrated probability scores may expose a probability")
        if (self.score_kind == ScoreKind.BASELINE) != (self.model_id is None):
            raise ValueError("Baseline has no model ID; supervised scores require one")
        if self.scored_at < self.as_of:
            raise ValueError("Scoring time cannot precede the feature cutoff")
        return self


class AlertEvidence(RunContext):
    alert_id: UUID
    snapshot_id: UUID
    score_id: UUID
    evidence_schema_version: Version
    canonical_payload: bytes
    original_hash: Digest
    created_at: UTCDateTime

    @model_validator(mode="after")
    def exact_payload_hash(self) -> Self:
        if (
            not self.canonical_payload
            or hashlib.sha256(self.canonical_payload).hexdigest() != self.original_hash
        ):
            raise ValueError("Evidence digest must match the exact nonempty payload bytes")
        return self


class RunResult(RunContext):
    region_id: UUID
    status: RunStatus
    completeness: Completeness
    stages: dict[Stage, StageStatus]
    completed_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def complete_result(self) -> Self:
        if self.status == RunStatus.COMPLETED:
            if (
                self.completed_at is None
                or self.completed_at < self.as_of
                or self.completeness == Completeness.UNAVAILABLE
                or any(self.stages.get(stage) != StageStatus.SUCCEEDED for stage in Stage)
            ):
                raise ValueError(
                    "Completed results require all stages, usable coverage and completion time"
                )
        return self
