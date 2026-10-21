"""api-v1 response and query contracts for the read-only frontend API."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator

from firewatch.schemas.common import (
    DTO,
    AnchorStatus,
    Completeness,
    JobStatus,
    JobType,
    Latitude,
    Longitude,
    Priority,
    Product,
    RunMode,
    RunStatus,
    ScoreKind,
    Stage,
    StageStatus,
    UTCDateTime,
)
from firewatch.schemas.contracts import BoundingBox

NOTICE = (
    "Research and decision-support prototype only. Satellite thermal anomalies and model "
    "priority scores can be wrong or incomplete. Do not use FireWatch AI for emergency "
    "response, preservation of life, or protection of property. Confirm important events "
    "with official authorities and operational fire services."
)


class APIResponse(DTO):
    schema_version: Literal["api-v1"] = "api-v1"
    research_notice: str = NOTICE


class ErrorInfo(DTO):
    code: str
    message: str
    request_id: str
    retryable: bool


class ErrorEnvelope(DTO):
    error: ErrorInfo


class Pagination(DTO):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=1_000_000)


class RunQuery(Pagination):
    region_id: UUID | None = None
    mode: RunMode | None = None
    status: RunStatus | None = None


class ScopeQuery(DTO):
    run_id: UUID | None = None


class HotspotFilters(DTO):
    run_id: UUID
    priority: Priority | None = None
    bbox: Annotated[str, Field(max_length=160)] | None = None

    @field_validator("bbox")
    @classmethod
    def validate_bbox(cls, value: str | None) -> str | None:
        if value is not None:
            parse_bbox(value)
        return value


class HotspotQuery(HotspotFilters, Pagination):
    pass


class GeoJSONQuery(HotspotFilters):
    limit: int = Field(default=2000, ge=1, le=2000)
    offset: int = Field(default=0, ge=0, le=1_000_000)


def parse_bbox(value: str) -> BoundingBox:
    values = value.split(",")
    if len(values) != 4:
        raise ValueError("Use west,south,east,north for bbox")
    return BoundingBox(
        west=float(values[0]), south=float(values[1]), east=float(values[2]), north=float(values[3])
    )


class Page[T](APIResponse):
    items: list[T]
    total: int
    limit: int
    offset: int


class Freshness(DTO):
    evaluated_at: UTCDateTime
    reference_time: UTCDateTime
    reference_kind: Literal["current_time", "historical_cutoff"]
    latest_detection_at: UTCDateTime | None
    latest_weather_valid_at: UTCDateTime | None
    latest_retrieved_at: UTCDateTime | None
    latest_scored_at: UTCDateTime | None
    completed_at: UTCDateTime | None
    observation_age_hours: float | None
    weather_age_hours: float | None
    observations_stale: bool | None
    weather_stale: bool | None
    quality_flags: list[str]


class RunContext(APIResponse):
    run_id: UUID
    mode: RunMode
    as_of: UTCDateTime
    data_completeness: Completeness
    freshness: Freshness


class RegionView(DTO):
    region_id: UUID
    name: str
    bbox: BoundingBox
    enabled: bool
    available_modes: list[RunMode]
    latest_live_run_id: UUID | None


class RunView(RunContext):
    region_id: UUID
    status: RunStatus
    config_hash: str
    scorer_version: str
    feature_version: str
    stages: dict[Stage, StageStatus]
    created_at: UTCDateTime
    started_at: UTCDateTime | None
    completed_at: UTCDateTime | None


class HotspotView(DTO):
    snapshot_id: UUID
    latitude: Latitude
    longitude: Longitude
    member_count: int
    latest_detection_at: UTCDateTime
    frp_max_mw: float | None = Field(
        ge=0, description="Maximum fire radiative power in MW; null means missing."
    )
    frp_sum_mw: float | None = Field(
        ge=0, description="Sum of fire radiative power in MW; null means missing."
    )
    priority: Priority
    priority_score: float = Field(
        ge=0, description="Version-specific ranking index; not an escalation probability."
    )
    score_kind: ScoreKind
    escalation_probability: float | None = Field(
        ge=0,
        le=1,
        description="Calibrated probability in [0,1] only when supported; otherwise null.",
    )
    scorer_version: str
    feature_version: str
    scored_at: UTCDateTime
    reasons: list[str]
    quality_flags: list[str]
    alert_id: UUID | None


class HotspotPage(RunContext):
    items: list[HotspotView]
    total: int
    limit: int
    offset: int


class Geometry(DTO):
    type: Literal["Point"] = "Point"
    coordinates: tuple[Longitude, Latitude] = Field(
        description="WGS84 decimal degrees in longitude, latitude order."
    )


class GeoJSONFeature(DTO):
    type: Literal["Feature"] = "Feature"
    id: UUID
    geometry: Geometry
    properties: HotspotView


class FeatureCollection(RunContext):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature]
    total: int
    returned: int
    limit: int
    offset: int
    truncated: bool


class Summary(RunContext):
    total: int
    priority_counts: dict[Priority, int]
    score_kind_counts: dict[ScoreKind, int]
    filters: HotspotFilters


class ModelMetadata(APIResponse):
    scorer_version: str
    score_kind: ScoreKind
    feature_version: str | None
    model_id: UUID | None
    status: Literal["not_implemented", "unavailable", "ready"]
    artifact_required: bool
    artifact_ready: bool
    scoring_available: bool
    probability_supported: bool
    limitations: list[str]


class SourceStatus(DTO):
    region_id: UUID
    product: Product
    status: Literal["never_fetched", "ok", "stale", "degraded", "unavailable"]
    last_attempt_at: UTCDateTime | None
    last_successful_retrieval_at: UTCDateTime | None
    latest_detection_at: UTCDateTime | None
    latest_attempt_succeeded: bool | None
    refresh_id: UUID | None = None
    coverage: Literal["complete", "partial", "unavailable"] | None = None
    fetched_count: int | None = None
    accepted_count: int | None = None
    rejected_count: int | None = None
    duplicate_count: int | None = None
    completed_chunks: int | None = None
    total_chunks: int | None = None
    next_retry_at: UTCDateTime | None = None


class RegionRefresh(DTO):
    region_id: UUID
    latest_live_run_id: UUID | None
    last_successful_refresh_at: UTCDateTime | None


class StatusView(APIResponse):
    evaluated_at: UTCDateTime
    sources: list[SourceStatus]
    refreshes: list[RegionRefresh]
    scorer: ModelMetadata
    firms_configured: bool
    chain_enabled: bool
    weather_latest_retrieved_at: UTCDateTime | None
    climate_baseline_count: int


class ReplayView(RunContext):
    scenario_id: str
    name: str
    description: str
    region_id: UUID
    scorer_version: str
    feature_version: str


class AnchorView(DTO):
    chain_id: int
    contract_address: str
    transaction_hash: str
    block_hash: str | None
    block_height: int | None
    confirmation_status: AnchorStatus
    network_kind: str


class Verification(RunContext):
    alert_id: UUID
    local_status: Literal["local_match", "local_mismatch"]
    original_hash: str
    computed_hash: str
    anchor_status: Literal[
        "unanchored", "anchor_pending", "anchor_match", "anchor_mismatch", "anchor_unavailable"
    ]
    anchors: list[AnchorView]
    artifact_verification: Literal["not_performed"] = "not_performed"


class AlertView(RunContext):
    alert_id: UUID
    snapshot_id: UUID
    score_id: UUID
    evidence_schema_version: str
    created_at: UTCDateTime
    original_hash: str
    payload: JsonValue
    payload_representation: Literal["public_projection"] = "public_projection"
    payload_redacted: bool
    verification: Verification


class HotspotDetail(RunContext):
    hotspot: HotspotView
    algorithm_version: str
    features: dict[str, JsonValue]
    missing_flags: dict[str, bool]
    units: dict[str, str] = Field(
        description="Feature-name to explicit unit mapping; missing values are not zero."
    )
    feature_hash: str
    explanation: dict[str, JsonValue]
    evidence: AlertView | None


class JobView(APIResponse):
    job_id: UUID
    type: JobType
    status: JobStatus
    attempts: int
    available_at: UTCDateTime
    lease_until: UTCDateTime | None
    heartbeat_at: UTCDateTime | None
    result_run_id: UUID | None
    stages: dict[Stage, StageStatus]
    failure: str | None


class Readiness(DTO):
    status: Literal["ready"] = "ready"
    database: Literal["ready"] = "ready"
    schema_revision: str
    artifact_ready: bool
