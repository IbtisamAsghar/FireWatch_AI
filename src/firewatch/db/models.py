"""Core persistence contract. PostgreSQL migrations additionally install integrity guards."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from firewatch.db.base import Base
from firewatch.schemas.common import (
    AnchorStatus,
    Completeness,
    Confidence,
    DayNight,
    JobStatus,
    JobType,
    ModelStatus,
    Priority,
    Product,
    Provider,
    RunMode,
    RunStatus,
    ScoreKind,
    Sensor,
)


def enum_type(kind: type[StrEnum]) -> sa.Enum:
    return sa.Enum(
        kind,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda values: [value.value for value in values],
    )


def finite(column: str, minimum: float = 0) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} >= {minimum} AND {column} < 'Infinity'::float8", name=f"{column}_range"
    )


def coordinates(lat: str = "latitude", lon: str = "longitude") -> tuple[sa.CheckConstraint, ...]:
    return (
        sa.CheckConstraint(f"{lat} BETWEEN -90 AND 90", name=f"{lat}_range"),
        sa.CheckConstraint(f"{lon} BETWEEN -180 AND 180", name=f"{lon}_range"),
    )


class Identity:
    id: Mapped[UUID] = mapped_column(
        sa.Uuid, primary_key=True, default=uuid4, server_default=sa.text("gen_random_uuid()")
    )


class Region(Identity, Base):
    __tablename__ = "regions"
    name: Mapped[str] = mapped_column(sa.String(100), unique=True)
    west: Mapped[float] = mapped_column(sa.Float)
    south: Mapped[float] = mapped_column(sa.Float)
    east: Mapped[float] = mapped_column(sa.Float)
    north: Mapped[float] = mapped_column(sa.Float)
    enabled: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    description: Mapped[str] = mapped_column(sa.String(500), default="", server_default="")
    __table_args__ = (
        sa.CheckConstraint("west >= -180 AND east <= 180 AND west < east", name="longitude_bounds"),
        sa.CheckConstraint(
            "south >= -90 AND north <= 90 AND south < north", name="latitude_bounds"
        ),
        sa.CheckConstraint("name ~ '^[a-z0-9]+(-[a-z0-9]+)*$'", name="name_slug"),
    )


class SourceAsset(Identity, Base):
    __tablename__ = "source_assets"
    provider: Mapped[Provider] = mapped_column(enum_type(Provider))
    product: Mapped[str] = mapped_column(sa.String(100))
    request_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    storage_path: Mapped[str] = mapped_column(sa.String(1000))
    content_hash: Mapped[str] = mapped_column(sa.String(64))
    format: Mapped[str] = mapped_column(sa.String(100))
    processing_version: Mapped[str] = mapped_column(sa.String(100))
    __table_args__ = (
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_format"),
        sa.CheckConstraint(
            "length(product)>0 AND length(storage_path)>0", name="required_metadata"
        ),
        sa.Index("ix_source_assets_provider_content_hash", "provider", "content_hash"),
    )


class IngestionBatch(Identity, Base):
    __tablename__ = "ingestion_batches"
    region_id: Mapped[UUID] = mapped_column(sa.ForeignKey("regions.id"))
    product: Mapped[Product] = mapped_column(enum_type(Product))
    window_start: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[JobStatus] = mapped_column(enum_type(JobStatus), default=JobStatus.QUEUED)
    asset_id: Mapped[UUID] = mapped_column(sa.ForeignKey("source_assets.id"))
    fetched_count: Mapped[int] = mapped_column(default=0, server_default="0")
    accepted_count: Mapped[int] = mapped_column(default=0, server_default="0")
    rejected_count: Mapped[int] = mapped_column(default=0, server_default="0")
    duplicate_count: Mapped[int] = mapped_column(default=0, server_default="0")
    failure_reason: Mapped[str | None] = mapped_column(sa.Text)
    __table_args__ = (
        sa.CheckConstraint("window_start < window_end", name="window_order"),
        sa.CheckConstraint(
            "fetched_count>=0 AND accepted_count>=0 AND rejected_count>=0 "
            "AND duplicate_count>=0 AND accepted_count+rejected_count+duplicate_count "
            "<= fetched_count",
            name="row_counts",
        ),
        sa.Index("ix_ingestion_batches_region_window", "region_id", "window_start"),
    )


class Detection(Identity, Base):
    __tablename__ = "detections"
    dedup_key: Mapped[str] = mapped_column(sa.String(64), unique=True)
    primary_batch_id: Mapped[UUID] = mapped_column(sa.ForeignKey("ingestion_batches.id"))
    product: Mapped[Product] = mapped_column(enum_type(Product))
    sensor: Mapped[Sensor] = mapped_column(enum_type(Sensor))
    latitude: Mapped[float] = mapped_column(sa.Float)
    longitude: Mapped[float] = mapped_column(sa.Float)
    acquired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    frp_mw: Mapped[float | None] = mapped_column(sa.Float)
    brightness_k: Mapped[float | None] = mapped_column(sa.Float)
    confidence_raw: Mapped[str | None] = mapped_column(sa.Text)
    confidence: Mapped[Confidence] = mapped_column(
        enum_type(Confidence), default=Confidence.UNKNOWN
    )
    daynight: Mapped[DayNight | None] = mapped_column(enum_type(DayNight))
    scan: Mapped[float | None] = mapped_column(sa.Float)
    track: Mapped[float | None] = mapped_column(sa.Float)
    raw_values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    __table_args__ = (
        *coordinates(),
        finite("frp_mw"),
        finite("brightness_k", 0.000001),
        finite("scan"),
        finite("track"),
        sa.CheckConstraint("dedup_key ~ '^[0-9a-f]{64}$'", name="dedup_key_format"),
        sa.CheckConstraint(
            "(product IN ('VIIRS_NOAA20_NRT','VIIRS_NOAA20_SP') "
            "AND sensor='NOAA-20') OR "
            "(product IN ('VIIRS_NOAA21_NRT','VIIRS_NOAA21_SP') "
            "AND sensor='NOAA-21')",
            name="sensor_product_match",
        ),
        sa.UniqueConstraint("id", "acquired_at"),
        sa.Index("ix_detections_acquired_at", "acquired_at"),
    )


class DetectionSource(Base):
    __tablename__ = "detection_sources"
    detection_id: Mapped[UUID] = mapped_column(sa.ForeignKey("detections.id"), primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(sa.ForeignKey("ingestion_batches.id"), primary_key=True)
    raw_values: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")


class DetectionRegion(Base):
    __tablename__ = "detection_regions"
    region_id: Mapped[UUID] = mapped_column(sa.ForeignKey("regions.id"), primary_key=True)
    detection_id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    acquired_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["detection_id", "acquired_at"], ["detections.id", "detections.acquired_at"]
        ),
        sa.Index("ix_detection_regions_region_acquired", "region_id", "acquired_at"),
    )


class WeatherRecord(Identity, Base):
    __tablename__ = "weather_records"
    cell_key: Mapped[str] = mapped_column(sa.String(100))
    latitude: Mapped[float] = mapped_column(sa.Float)
    longitude: Mapped[float] = mapped_column(sa.Float)
    requested_latitude: Mapped[float] = mapped_column(sa.Float)
    requested_longitude: Mapped[float] = mapped_column(sa.Float)
    provider: Mapped[Provider] = mapped_column(enum_type(Provider))
    model: Mapped[str] = mapped_column(sa.String(100))
    product: Mapped[str] = mapped_column(sa.String(100))
    source_version: Mapped[str] = mapped_column(sa.String(100))
    valid_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    temperature_c: Mapped[float | None] = mapped_column(sa.Float)
    humidity_pct: Mapped[float | None] = mapped_column(sa.Float)
    wind_ms: Mapped[float | None] = mapped_column(sa.Float)
    gust_ms: Mapped[float | None] = mapped_column(sa.Float)
    precipitation_mm: Mapped[float | None] = mapped_column(sa.Float)
    units: Mapped[dict[str, Any]] = mapped_column(JSONB)
    quality_flags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    asset_id: Mapped[UUID] = mapped_column(sa.ForeignKey("source_assets.id"))
    __table_args__ = (
        *coordinates(),
        *coordinates("requested_latitude", "requested_longitude"),
        finite("temperature_c", -273.15),
        finite("wind_ms"),
        finite("gust_ms"),
        finite("precipitation_mm"),
        sa.CheckConstraint("humidity_pct BETWEEN 0 AND 100", name="humidity_range"),
        sa.UniqueConstraint(
            "provider",
            "model",
            "product",
            "cell_key",
            "valid_at",
            "source_version",
            name="uq_weather_lookup",
        ),
    )


class ClimateBaselineRecord(Identity, Base):
    __tablename__ = "climate_baselines"
    cell_key: Mapped[str] = mapped_column(sa.String(100))
    latitude: Mapped[float] = mapped_column(sa.Float)
    longitude: Mapped[float] = mapped_column(sa.Float)
    source: Mapped[Provider] = mapped_column(enum_type(Provider))
    reference_start_year: Mapped[int] = mapped_column()
    reference_end_year: Mapped[int] = mapped_column()
    month: Mapped[int] = mapped_column()
    statistics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    temperature_mean_c: Mapped[float | None] = mapped_column(sa.Float)
    precipitation_mean_mm: Mapped[float | None] = mapped_column(sa.Float)
    sample_counts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[str] = mapped_column(sa.String(100))
    asset_id: Mapped[UUID] = mapped_column(sa.ForeignKey("source_assets.id"))
    __table_args__ = (
        *coordinates(),
        finite("temperature_mean_c", -273.15),
        finite("precipitation_mean_mm"),
        sa.CheckConstraint("month BETWEEN 1 AND 12", name="month_range"),
        sa.CheckConstraint(
            "reference_start_year BETWEEN 1900 AND 2100 AND "
            "reference_end_year BETWEEN reference_start_year AND 2100",
            name="reference_order",
        ),
        sa.UniqueConstraint(
            "cell_key",
            "source",
            "reference_start_year",
            "reference_end_year",
            "month",
            "version",
            name="uq_climate_reference",
        ),
    )


class Run(Identity, Base):
    __tablename__ = "runs"
    region_id: Mapped[UUID] = mapped_column(sa.ForeignKey("regions.id"))
    mode: Mapped[RunMode] = mapped_column(enum_type(RunMode))
    as_of: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    status: Mapped[RunStatus] = mapped_column(enum_type(RunStatus), default=RunStatus.QUEUED)
    config_hash: Mapped[str] = mapped_column(sa.String(64))
    run_key: Mapped[str] = mapped_column(sa.String(200), unique=True)
    input_asset_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    completeness: Mapped[Completeness] = mapped_column(
        enum_type(Completeness), default=Completeness.UNAVAILABLE
    )
    stage_status: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    scorer_version: Mapped[str] = mapped_column(sa.String(100))
    feature_version: Mapped[str] = mapped_column(sa.String(100))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    __table_args__ = (
        sa.UniqueConstraint("id", "region_id"),
        sa.CheckConstraint("config_hash ~ '^[0-9a-f]{64}$'", name="config_hash_format"),
        sa.CheckConstraint(
            "completed_at IS NULL OR (started_at IS NOT NULL "
            "AND completed_at >= started_at AND completed_at >= as_of)",
            name="completion_time",
        ),
        sa.CheckConstraint(
            "status != 'completed' OR (completed_at IS NOT NULL AND completeness != 'unavailable')",
            name="completed_metadata",
        ),
        sa.Index("ix_runs_region_mode_cutoff", "region_id", "mode", "as_of"),
    )


class LiveRunHead(Base):
    __tablename__ = "live_run_heads"
    region_id: Mapped[UUID] = mapped_column(sa.ForeignKey("regions.id"), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(sa.Uuid, unique=True)
    __table_args__ = (
        sa.ForeignKeyConstraint(["run_id", "region_id"], ["runs.id", "runs.region_id"]),
    )


class Job(Identity, Base):
    __tablename__ = "jobs"
    type: Mapped[JobType] = mapped_column(enum_type(JobType))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(sa.String(200), unique=True)
    payload_hash: Mapped[str] = mapped_column(sa.String(64))
    status: Mapped[JobStatus] = mapped_column(enum_type(JobStatus), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    lease_until: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    result_run_id: Mapped[UUID | None] = mapped_column(sa.ForeignKey("runs.id"))
    error: Mapped[str | None] = mapped_column(sa.Text)
    __table_args__ = (
        sa.CheckConstraint("attempts >= 0", name="attempts_range"),
        sa.CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="payload_hash_format"),
        sa.Index("ix_jobs_due", "status", "available_at"),
    )


class ClusterSnapshotRecord(Identity, Base):
    __tablename__ = "cluster_snapshots"
    run_id: Mapped[UUID] = mapped_column(sa.ForeignKey("runs.id"))
    latitude: Mapped[float] = mapped_column(sa.Float)
    longitude: Mapped[float] = mapped_column(sa.Float)
    member_count: Mapped[int] = mapped_column()
    frp_max_mw: Mapped[float | None] = mapped_column(sa.Float)
    frp_sum_mw: Mapped[float | None] = mapped_column(sa.Float)
    latest_detection_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    algorithm_version: Mapped[str] = mapped_column(sa.String(100))
    __table_args__ = (
        *coordinates(),
        finite("frp_max_mw"),
        finite("frp_sum_mw"),
        sa.CheckConstraint("member_count > 0", name="member_count_range"),
        sa.CheckConstraint(
            "frp_max_mw IS NULL OR frp_sum_mw IS NULL OR frp_sum_mw >= frp_max_mw",
            name="frp_aggregates",
        ),
        sa.UniqueConstraint("id", "run_id"),
        sa.Index("ix_cluster_snapshots_run_id", "run_id"),
    )


class ClusterMember(Base):
    __tablename__ = "cluster_members"
    snapshot_id: Mapped[UUID] = mapped_column(
        sa.ForeignKey("cluster_snapshots.id"), primary_key=True
    )
    detection_id: Mapped[UUID] = mapped_column(sa.ForeignKey("detections.id"), primary_key=True)


class FeatureSetRecord(Base):
    __tablename__ = "feature_sets"
    snapshot_id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(sa.Uuid)
    feature_version: Mapped[str] = mapped_column(sa.String(100))
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    missing_flags: Mapped[dict[str, Any]] = mapped_column(JSONB)
    units: Mapped[dict[str, Any]] = mapped_column(JSONB)
    lineage: Mapped[dict[str, Any]] = mapped_column(JSONB)
    feature_hash: Mapped[str] = mapped_column(sa.String(64))
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["snapshot_id", "run_id"], ["cluster_snapshots.id", "cluster_snapshots.run_id"]
        ),
        sa.CheckConstraint("feature_hash ~ '^[0-9a-f]{64}$'", name="feature_hash_format"),
    )


class ModelVersion(Identity, Base):
    __tablename__ = "model_versions"
    version: Mapped[str] = mapped_column(sa.String(100), unique=True)
    artifact_path: Mapped[str] = mapped_column(sa.String(1000))
    artifact_hash: Mapped[str] = mapped_column(sa.String(64))
    feature_version: Mapped[str] = mapped_column(sa.String(100))
    training_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB)
    target_definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[ModelStatus] = mapped_column(
        enum_type(ModelStatus), default=ModelStatus.CANDIDATE
    )
    probability_supported: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    __table_args__ = (
        sa.CheckConstraint("artifact_hash ~ '^[0-9a-f]{64}$'", name="artifact_hash_format"),
        sa.UniqueConstraint("id", "version"),
    )


class Score(Identity, Base):
    __tablename__ = "scores"
    snapshot_id: Mapped[UUID] = mapped_column(sa.Uuid)
    run_id: Mapped[UUID] = mapped_column(sa.Uuid)
    scorer_version: Mapped[str] = mapped_column(sa.String(100))
    model_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    priority_score: Mapped[float] = mapped_column(sa.Float)
    score_kind: Mapped[ScoreKind] = mapped_column(enum_type(ScoreKind))
    escalation_probability: Mapped[float | None] = mapped_column(sa.Float)
    priority: Mapped[Priority] = mapped_column(enum_type(Priority))
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB)
    scored_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True))
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["snapshot_id", "run_id"], ["cluster_snapshots.id", "cluster_snapshots.run_id"]
        ),
        sa.ForeignKeyConstraint(
            ["model_id", "scorer_version"], ["model_versions.id", "model_versions.version"]
        ),
        sa.UniqueConstraint("snapshot_id", "scorer_version"),
        sa.UniqueConstraint("id", "snapshot_id", "run_id"),
        finite("priority_score"),
        sa.CheckConstraint("escalation_probability BETWEEN 0 AND 1", name="probability_range"),
        sa.CheckConstraint(
            "(score_kind='probability' AND escalation_probability IS NOT NULL) OR "
            "(score_kind!='probability' AND escalation_probability IS NULL)",
            name="probability_claim",
        ),
        sa.CheckConstraint(
            "(score_kind='baseline' AND model_id IS NULL) OR "
            "(score_kind!='baseline' AND model_id IS NOT NULL)",
            name="model_reference",
        ),
        sa.Index("ix_scores_run_priority_score", "run_id", "priority", "priority_score"),
    )


class Alert(Identity, Base):
    __tablename__ = "alerts"
    snapshot_id: Mapped[UUID] = mapped_column(sa.Uuid)
    run_id: Mapped[UUID] = mapped_column(sa.Uuid)
    score_id: Mapped[UUID] = mapped_column(sa.Uuid, unique=True)
    canonical_payload: Mapped[bytes] = mapped_column(sa.LargeBinary)
    original_hash: Mapped[str] = mapped_column(sa.String(64))
    evidence_schema_version: Mapped[str] = mapped_column(sa.String(100))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["score_id", "snapshot_id", "run_id"],
            ["scores.id", "scores.snapshot_id", "scores.run_id"],
        ),
        sa.CheckConstraint("original_hash ~ '^[0-9a-f]{64}$'", name="original_hash_format"),
        sa.CheckConstraint(
            "octet_length(canonical_payload)>0 AND "
            "original_hash=encode(sha256(canonical_payload),'hex')",
            name="payload_hash_match",
        ),
    )


class AnchorOutbox(Identity, Base):
    __tablename__ = "anchor_outbox"
    alert_id: Mapped[UUID] = mapped_column(sa.ForeignKey("alerts.id"))
    network: Mapped[str] = mapped_column(sa.String(100))
    status: Mapped[AnchorStatus] = mapped_column(
        enum_type(AnchorStatus), default=AnchorStatus.QUEUED
    )
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    available_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now()
    )
    tx_hash: Mapped[str | None] = mapped_column(sa.String(66))
    receipt_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default="{}"
    )
    error: Mapped[str | None] = mapped_column(sa.Text)
    __table_args__ = (
        sa.UniqueConstraint("alert_id", "network"),
        sa.CheckConstraint("attempts >= 0", name="attempts_range"),
        sa.Index("ix_anchor_outbox_due", "status", "available_at"),
    )


class Anchor(Identity, Base):
    __tablename__ = "anchors"
    alert_id: Mapped[UUID] = mapped_column(sa.ForeignKey("alerts.id"))
    chain_id: Mapped[int] = mapped_column(sa.BigInteger)
    contract_address: Mapped[str] = mapped_column(sa.String(42))
    transaction_hash: Mapped[str] = mapped_column(sa.String(66))
    block_hash: Mapped[str | None] = mapped_column(sa.String(66))
    block_height: Mapped[int | None] = mapped_column(sa.BigInteger)
    anchored_digest: Mapped[str] = mapped_column(sa.String(64))
    confirmation_status: Mapped[AnchorStatus] = mapped_column(enum_type(AnchorStatus))
    network_kind: Mapped[str] = mapped_column(sa.String(20))
    __table_args__ = (
        sa.UniqueConstraint("alert_id", "chain_id", "contract_address"),
        sa.CheckConstraint("chain_id>0 AND block_height>=0", name="chain_numbers"),
        sa.CheckConstraint("contract_address ~ '^0x[0-9a-fA-F]{40}$'", name="contract_format"),
        sa.CheckConstraint("transaction_hash ~ '^0x[0-9a-fA-F]{64}$'", name="transaction_format"),
        sa.CheckConstraint("block_hash ~ '^0x[0-9a-fA-F]{64}$'", name="block_hash_format"),
        sa.CheckConstraint("anchored_digest ~ '^[0-9a-f]{64}$'", name="digest_format"),
        sa.CheckConstraint("network_kind IN ('local','testnet','mainnet')", name="network_kind"),
    )
