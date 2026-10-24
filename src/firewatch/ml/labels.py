"""Versioned observed-FRP target; pure labeling never modifies feature inputs."""

import math
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

import yaml
from pydantic import ConfigDict, Field, model_validator

from firewatch.processing.clusters import EARTH_RADIUS_KM, ClusterDetails, distance_km
from firewatch.processing.features import FeatureValues, validate_feature_set
from firewatch.processing.suitability import centroid_target_exclusions
from firewatch.repositories.revisions import RevisionPolicy, SelectedDetection
from firewatch.schemas.common import DTO, Digest, JobStatus, Product, Sensor, UTCDateTime
from firewatch.schemas.contracts import BoundingBox, FeatureSet
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import utc

TARGET_VERSION: Literal["observed-frp-strengthening-v1"] = "observed-frp-strengthening-v1"
PRIMARY = {Sensor.NOAA20: Product.NOAA20_SP}


class TargetSpec(DTO):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["target-spec-v1"] = "target-spec-v1"
    target_version: Literal["observed-frp-strengthening-v1"] = TARGET_VERSION
    radius_km: float = Field(default=2.0, gt=0, strict=True)
    horizon_hours: int = Field(default=24, gt=0, strict=True)
    frp_increase_min_mw: float = Field(default=5.0, gt=0, strict=True)
    frp_increase_fraction: float = Field(default=0.20, gt=0, strict=True)
    cadence_hours: int = Field(default=6, gt=0, strict=True)
    cadence_anchor: UTCDateTime = datetime(2022, 1, 1, tzinfo=UTC)
    feature_version: Literal["features-v1"] = "features-v1"
    target_input_policy: Literal["compact-centroid-inputs-v1"] = "compact-centroid-inputs-v1"
    product: Literal[Product.NOAA20_SP] = Product.NOAA20_SP
    comparison_policy: Literal["separate_sensor_product_cohorts"] = (
        "separate_sensor_product_cohorts"
    )
    window: Literal["(T,T+24h]"] = "(T,T+24h]"
    evaluation_kind: Literal["retrospective_observation_proxy"] = "retrospective_observation_proxy"

    @model_validator(mode="after")
    def frozen_v1(self) -> Self:
        if (
            self.radius_km,
            self.horizon_hours,
            self.frp_increase_min_mw,
            self.frp_increase_fraction,
            self.cadence_hours,
            self.cadence_anchor,
        ) != (2.0, 24, 5.0, 0.20, 6, datetime(2022, 1, 1, tzinfo=UTC)):
            raise ValueError("Changed target parameters require a reviewed new target version")
        return self

    @property
    def sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


def load_target_spec(path: Path) -> TargetSpec:
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("Target specification exceeds size bound")
    return TargetSpec.model_validate(yaml.safe_load(raw))


class DatasetPeriod(DTO):
    start: UTCDateTime
    end: UTCDateTime

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Dataset period must be ordered and half-open")
        return self


class DatasetDesign(DTO):
    schema_version: Literal["target-dataset-design-v1", "target-dataset-design-2025-holdout-v1"] = (
        "target-dataset-design-v1"
    )
    target_spec_sha256: Digest
    region: Literal["northern-california"] = "northern-california"
    bbox: BoundingBox = Field(
        default_factory=lambda: BoundingBox(west=-122.5, south=38.5, east=-120.0, north=40.5)
    )
    periods: Annotated[list[DatasetPeriod], Field(min_length=1, max_length=10)]
    feature_lookback_hours: Literal[24] = 24
    reference_years: tuple[Literal[2010], Literal[2019]] = (2010, 2019)
    split_policy: Literal["deferred_to_phase_4_2", "historical-2025-independent-holdout-v1"] = (
        "deferred_to_phase_4_2"
    )
    final_test_access: Literal["forbidden_during_target_design"] = "forbidden_during_target_design"

    @model_validator(mode="after")
    def frozen_design(self) -> Self:
        if self.bbox != BoundingBox(west=-122.5, south=38.5, east=-120.0, north=40.5):
            raise ValueError("Changed study bbox requires a reviewed new dataset design")
        if self.target_spec_sha256 != TargetSpec().sha256:
            raise ValueError("Dataset design is not bound to the frozen target")
        extended = self.schema_version == "target-dataset-design-2025-holdout-v1"
        policy = "historical-2025-independent-holdout-v1" if extended else "deferred_to_phase_4_2"
        if self.split_policy != policy:
            raise ValueError("Dataset version and frozen split policy must agree")
        years = (2022, 2024, 2025) if extended else (2022, 2024)
        expected = [(datetime(y, 5, 1, tzinfo=UTC), datetime(y, 11, 1, tzinfo=UTC)) for y in years]
        if [(p.start, p.end) for p in self.periods] != expected:
            raise ValueError("Changed season design requires a reviewed new design version")
        return self


def training_cutoffs(start: datetime, end: datetime, spec: TargetSpec) -> list[datetime]:
    start, end = utc(start), utc(end)
    if start >= end or end - start > timedelta(days=3660):
        raise ValueError("Choose an ordered dataset window of at most ten years")
    step = spec.cadence_hours * 3600
    anchor = spec.cadence_anchor
    seconds = (start - anchor).total_seconds()
    first = anchor + timedelta(seconds=math.ceil(seconds / step) * step)
    return [
        first + timedelta(seconds=i * step)
        for i in range(max(0, math.ceil((end - first).total_seconds() / step)))
    ]


def on_cadence(cutoff: datetime, spec: TargetSpec) -> bool:
    return (utc(cutoff) - spec.cadence_anchor) % timedelta(hours=spec.cadence_hours) == timedelta(0)


class Outage(DTO):
    product: Product
    start: UTCDateTime
    end: UTCDateTime
    evidence_reference: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Outage interval must be ordered [start,end)")
        return self


class OutageCatalog(DTO):
    schema_version: Literal["label-outages-v1"] = "label-outages-v1"
    outages: list[Outage] = Field(default_factory=list, max_length=1000)


class DaySource(DTO):
    day: date
    product: Product
    batch_id: UUID
    asset_id: UUID
    source_sha256: Digest
    retrieved_at: UTCDateTime
    window_start: UTCDateTime
    window_end: UTCDateTime
    status: JobStatus
    rejected_count: int = Field(ge=0)

    @property
    def complete(self) -> bool:
        midnight = datetime.combine(self.day, time.min, UTC)
        return (
            self.status == JobStatus.SUCCEEDED
            and self.rejected_count == 0
            and self.window_start <= midnight
            and self.window_end >= midnight + timedelta(days=1)
            and self.retrieved_at >= midnight + timedelta(days=1)
        )


class FollowupEvidence(DTO):
    schema_version: Literal["target-followup-v1"] = "target-followup-v1"
    region_id: UUID
    bbox: BoundingBox
    as_of: UTCDateTime
    end: UTCDateTime
    retrieval_cutoff: UTCDateTime
    source_days: list[DaySource] = Field(default_factory=list, max_length=16)
    policy: RevisionPolicy | None = None
    observations: list[SelectedDetection] = Field(default_factory=list, max_length=100000)
    outages: list[Outage] = Field(default_factory=list, max_length=1000)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.end != self.as_of + timedelta(hours=24):
            raise ValueError("Follow-up must end exactly 24 hours after T")
        keys = [(r.product, r.day) for r in self.source_days]
        if len(set(keys)) != len(keys) or any(
            r.retrieved_at > self.retrieval_cutoff for r in self.source_days
        ):
            raise ValueError("Duplicate or unavailable follow-up source day")
        if self.policy and (
            self.policy.products != PRIMARY or self.policy.retrieval_cutoff != self.retrieval_cutoff
        ):
            raise ValueError(
                "Follow-up policy must pin the primary sensor/product and availability"
            )
        complete_ids = {r.batch_id for r in self.source_days if r.complete}
        if (self.policy is None and (self.observations or complete_ids)) or (
            self.policy and set(self.policy.batch_ids) != complete_ids
        ):
            raise ValueError("Follow-up policy/source-day pins disagree")
        ids, physical = set(), set()
        for row in self.observations:
            obs = row.observation
            normalization = obs.raw_values.get("normalization")
            if (
                row.detection_id in ids
                or row.physical_key in physical
                or row.batch_id not in complete_ids
                or obs.product != Product.NOAA20_SP
                or obs.sensor != Sensor.NOAA20
                or digest(obs.model_dump(mode="json")) != row.revision_hash
                or not isinstance(normalization, dict)
                or normalization.get("physical_key") != row.physical_key
                or not (
                    self.bbox.west <= obs.longitude <= self.bbox.east
                    and self.bbox.south <= obs.latitude <= self.bbox.north
                )
            ):
                raise ValueError("Invalid, duplicated or mixed follow-up observation revision")
            ids.add(row.detection_id)
            physical.add(row.physical_key)
        return self


class TargetInput(DTO):
    products: dict[Sensor, Product]
    region_name: str = Field(min_length=1, max_length=100)
    region_id: UUID
    detail: ClusterDetails
    feature: FeatureSet


class LabelResult(DTO):
    schema_version: Literal["observed-frp-label-v1"] = "observed-frp-label-v1"
    target_version: Literal["observed-frp-strengthening-v1"] = TARGET_VERSION
    target_spec_sha256: Digest
    run_id: UUID
    snapshot_id: UUID
    as_of: UTCDateTime
    feature_hash: Digest
    followup_sha256: Digest
    baseline_products: dict[Sensor, Product]
    label: Literal["positive", "negative", "unknown"]
    target_value: Literal[0, 1] | None
    threshold_mw: float | None
    unobserved_followup: bool | None
    unknown_reasons: list[str]
    dataset_exclusions: list[str]
    later_detection_count: int = Field(ge=0)
    valid_later_frp_count: int = Field(ge=0)
    max_later_frp_mw: float | None
    stronger_detection_ids: list[UUID]
    acquisition_hours_utc: dict[str, int]


def followup_days(cutoff: datetime) -> list[date]:
    cutoff = utc(cutoff)
    end = cutoff + timedelta(hours=24)
    # The final instant is included, so its calendar day also needs coverage.
    return [cutoff.date() + timedelta(days=i) for i in range((end.date() - cutoff.date()).days + 1)]


def footprint_covered(bbox: BoundingBox, latitude: float, longitude: float) -> bool:
    angular = 2.0 / EARTH_RADIUS_KM
    dy = math.degrees(angular)
    if latitude - dy < bbox.south or latitude + dy > bbox.north:
        return False
    ratio = math.sin(angular) / math.cos(math.radians(latitude))
    if abs(ratio) >= 1:
        return bbox.west == -180 and bbox.east == 180
    dx = math.degrees(math.asin(ratio))
    return longitude - dx >= bbox.west and longitude + dx <= bbox.east


def label_snapshot(
    item: TargetInput, evidence: FollowupEvidence, spec: TargetSpec | None = None
) -> LabelResult:
    """Complete follow-up first; strict FRP increase and closed spherical radius."""
    spec = TargetSpec.model_validate((spec or TargetSpec()).model_dump())
    item = TargetInput.model_validate(item.model_dump())
    evidence = FollowupEvidence.model_validate(evidence.model_dump())
    feature = validate_feature_set(item.feature)
    snapshot = item.detail.snapshot
    if evidence.region_id != item.region_id or evidence.as_of != feature.as_of:
        raise ValueError("Label feature/region/follow-up context mismatch")
    reasons = centroid_target_exclusions(item.detail, feature)
    values = FeatureValues.model_validate(feature.values)
    member_counts = feature.lineage.get("valid_member_counts")
    if (
        snapshot.frp_max_mw != values.frp_max_mw
        or item.detail.frp_valid_members + item.detail.frp_missing_members
        != len(snapshot.member_ids)
        or not isinstance(member_counts, dict)
        or member_counts.get("frp") != item.detail.frp_valid_members
    ):
        raise ValueError("Current FRP diagnostics disagree with frozen features")
    if item.products != PRIMARY:
        reasons.append("unsupported_baseline_product")
    if item.detail.sensor_counts.get(Sensor.NOAA21, 0):
        reasons.append("unsupported_baseline_sensor")
    if not footprint_covered(evidence.bbox, snapshot.latitude, snapshot.longitude):
        reasons.append("target_footprint_outside_source_bbox")
    coverage = {(r.product, r.day) for r in evidence.source_days if r.complete}
    if any((spec.product, day) not in coverage for day in followup_days(feature.as_of)):
        reasons.append("incomplete_followup_archive")
    if any(
        o.product == spec.product and o.start <= evidence.end and o.end > feature.as_of
        for o in evidence.outages
    ):
        reasons.append("known_provider_outage")
    later = [
        r
        for r in evidence.observations
        if feature.as_of < r.observation.acquired_at <= evidence.end
        and distance_km(
            snapshot.latitude, snapshot.longitude, r.observation.latitude, r.observation.longitude
        )
        <= spec.radius_km
    ]
    later.sort(key=lambda r: (r.observation.acquired_at, r.detection_id.hex))
    valid = [r for r in later if r.observation.frp_mw is not None]
    baseline = values.frp_max_mw
    threshold = None
    stronger = []
    if baseline is not None:
        current = Decimal(str(baseline))
        exact = current + max(
            Decimal(str(spec.frp_increase_min_mw)),
            Decimal(str(spec.frp_increase_fraction)) * current,
        )
        if math.isfinite(float(exact)):
            threshold = float(exact)
            stronger = [r.detection_id for r in valid if Decimal(str(r.observation.frp_mw)) > exact]
        else:
            reasons.append("invalid_frp_threshold")
    if not stronger and len(valid) < len(later):
        reasons.append("missing_followup_frp")
    label: Literal["positive", "negative", "unknown"] = (
        "unknown" if reasons else "positive" if stronger else "negative"
    )
    return LabelResult(
        baseline_products=item.products,
        target_spec_sha256=spec.sha256,
        run_id=feature.run_id,
        snapshot_id=feature.snapshot_id,
        as_of=feature.as_of,
        feature_hash=feature.feature_hash,
        followup_sha256=digest(evidence.model_dump(mode="json")),
        label=label,
        target_value=None if label == "unknown" else int(label == "positive"),
        threshold_mw=threshold,
        unobserved_followup=None if label == "unknown" else label == "negative" and not later,
        unknown_reasons=sorted(set(reasons)),
        dataset_exclusions=[] if on_cadence(feature.as_of, spec) else ["off_six_hour_cadence"],
        later_detection_count=len(later),
        valid_later_frp_count=len(valid),
        max_later_frp_mw=max(
            float(r.observation.frp_mw) for r in valid if r.observation.frp_mw is not None
        )
        if valid
        else None,
        stronger_detection_ids=stronger,
        acquisition_hours_utc=dict(
            sorted(Counter(f"{r.observation.acquired_at.hour:02d}" for r in later).items())
        ),
    )
