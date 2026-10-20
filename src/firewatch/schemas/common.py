"""Versioned value types shared by ingestion, processing, scoring and delivery."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field


def timestamp_input(value: object) -> object:
    if not isinstance(value, str | datetime):
        raise ValueError("Use an aware ISO-8601 timestamp, not an epoch number")
    return value


UTCDateTime = Annotated[
    AwareDatetime,
    BeforeValidator(timestamp_input),
    AfterValidator(lambda value: value.astimezone(UTC)),
]
Latitude = Annotated[float, Field(ge=-90, le=90, allow_inf_nan=False)]
Longitude = Annotated[float, Field(ge=-180, le=180, allow_inf_nan=False)]
Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Version = Annotated[str, Field(min_length=1, max_length=100)]


class DTO(BaseModel):
    model_config = ConfigDict(
        extra="forbid", from_attributes=True, allow_inf_nan=False, hide_input_in_errors=True
    )


class Product(StrEnum):
    NOAA20_NRT = "VIIRS_NOAA20_NRT"
    NOAA21_NRT = "VIIRS_NOAA21_NRT"
    NOAA20_SP = "VIIRS_NOAA20_SP"
    NOAA21_SP = "VIIRS_NOAA21_SP"


class Sensor(StrEnum):
    NOAA20 = "NOAA-20"
    NOAA21 = "NOAA-21"


class Confidence(StrEnum):
    LOW = "low"
    NOMINAL = "nominal"
    HIGH = "high"
    UNKNOWN = "unknown"


class DayNight(StrEnum):
    DAY = "D"
    NIGHT = "N"


class Provider(StrEnum):
    FIRMS = "firms"
    OPEN_METEO = "open_meteo"
    POWER = "nasa_power"


class RunMode(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class JobType(StrEnum):
    REFRESH = "refresh"
    REPLAY = "replay"
    ANCHOR = "anchor"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Stage(StrEnum):
    INGEST = "ingest"
    SNAPSHOTS = "snapshots"
    ENRICHMENT = "enrichment"
    FEATURES = "features"
    SCORING = "scoring"
    EVIDENCE = "evidence"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ScoreKind(StrEnum):
    BASELINE = "baseline"
    MODEL = "model_score"
    PROBABILITY = "probability"


class Priority(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ModelStatus(StrEnum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    RETIRED = "retired"


class AnchorStatus(StrEnum):
    QUEUED = "queued"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
