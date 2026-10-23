"""Pure, validated evidence construction from frozen scoring inputs; no I/O."""

from datetime import timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import Field, model_validator

from firewatch.evidence.canonical import canonical_bytes, evidence_hash
from firewatch.evidence.serialization import serialize_evidence
from firewatch.ml.inference_bundle import ApprovedScorer
from firewatch.processing.features import (
    AssetPin,
    FeatureSpec,
    SatelliteAssetPin,
    validate_feature_set,
)
from firewatch.processing.weather import weather_at_cutoff
from firewatch.repositories.features import EnrichmentPins
from firewatch.repositories.revisions import RevisionPolicy
from firewatch.schemas.common import DTO, Digest, Priority, RunMode, ScoreKind, UTCDateTime
from firewatch.schemas.contracts import (
    BoundingBox,
    ClimateBaseline,
    ClusterSnapshot,
    FeatureSet,
    ScoreResult,
    WeatherSeries,
)
from firewatch.sources.firms_normalize import digest


class EvidenceRegion(DTO):
    region_id: UUID
    name: str = Field(min_length=1, max_length=100)
    bbox: BoundingBox


class EvidenceRevision(DTO):
    detection_id: UUID
    batch_id: UUID
    revision_hash: Digest


class SatelliteEvidence(DTO):
    policy: RevisionPolicy
    window_start: UTCDateTime
    selected: list[EvidenceRevision] = Field(max_length=500000)
    assets: list[SatelliteAssetPin]
    snapshot_input_hash: Digest


class WeatherEvidence(DTO):
    cell_key: str
    product: str
    model: str
    valid_at: UTCDateTime | None
    precipitation_interval_end: UTCDateTime
    records_sha256: Digest
    series: WeatherSeries
    assets: list[AssetPin]


class ModelEvidence(DTO):
    model_id: UUID
    scorer_version: str
    inference_manifest_sha256: Digest
    approval_reference: str


class EvidencePayload(DTO):
    schema_version: Literal["evidence-v1"] = "evidence-v1"
    canonicalization_version: Literal["evidence-json-v1"] = "evidence-json-v1"
    alert_id: UUID
    run_id: UUID
    snapshot_id: UUID
    score_id: UUID
    mode: RunMode
    region: EvidenceRegion
    as_of: UTCDateTime
    snapshot: ClusterSnapshot
    feature_version: str
    feature_hash: Digest
    feature: FeatureSet
    feature_payload_json: str
    scorer_version: str
    model_id: UUID | None
    model: ModelEvidence | None
    score_kind: ScoreKind
    priority_score: float
    escalation_probability: float | None
    priority: Priority
    explanation: dict[str, Any]
    reasons: list[str]
    warnings: list[str]
    scored_at: UTCDateTime
    satellite: SatelliteEvidence
    weather: WeatherEvidence
    baseline_version: str | None
    baseline: ClimateBaseline | None
    baseline_asset: AssetPin | None
    source_checksums: list[AssetPin]
    retrieval_cutoff: UTCDateTime
    run_config_hash: Digest
    feature_stage_hash: Digest

    @model_validator(mode="after")
    def consistent(self) -> "EvidencePayload":
        feature = validate_feature_set(self.feature)
        if self.satellite.window_start != self.as_of - timedelta(hours=24):
            raise ValueError("Evidence must bind the complete 24-hour satellite input window")
        if self.satellite.policy.retrieval_cutoff > self.retrieval_cutoff:
            raise ValueError("Evidence source retrieval cutoffs disagree")
        expected = (self.run_id, self.snapshot_id, self.mode, self.as_of, self.feature_version)
        for child in (feature, self.snapshot):
            if (
                child.run_id,
                child.snapshot_id,
                child.mode,
                child.as_of,
                child.feature_version,
            ) != expected:
                raise ValueError("Evidence context differs from frozen inputs")
        preimage = canonical_bytes(feature.model_dump(mode="json", exclude={"feature_hash"}))
        if (
            self.feature_hash != feature.feature_hash
            or self.feature_payload_json != preimage.decode()
        ):
            raise ValueError("Evidence feature hash/preimage mismatch")
        if self.alert_id != uuid5(self.score_id, "evidence-v1") or self.scored_at < self.as_of:
            raise ValueError("Evidence alert identity or scoring time mismatch")
        lineage: dict[str, Any] = feature.lineage
        if self.satellite.snapshot_input_hash != lineage["snapshot_input_hash"]:
            raise ValueError("Evidence satellite input hash mismatch")
        revisions = self.satellite.selected
        selected = {str(r.detection_id): r.model_dump(mode="json") for r in revisions}
        if len(selected) != len(revisions) or any(
            selected.get(r["detection_id"]) != r for r in lineage["member_revisions"]
        ):
            raise ValueError("Evidence member revisions differ from frozen selection")
        if set(self.snapshot.member_ids) != {
            UUID(r["detection_id"]) for r in lineage["member_revisions"]
        }:
            raise ValueError("Evidence snapshot members differ from feature lineage")
        batch_ids = {p.batch_id for p in self.satellite.assets}
        if (
            len(batch_ids) != len(self.satellite.assets)
            or batch_ids != set(self.satellite.policy.batch_ids)
            or any(r.batch_id not in batch_ids for r in revisions)
        ):
            raise ValueError("Evidence satellite batch/source coverage mismatch")
        if {p.batch_id: p.model_dump(mode="json") for p in self.satellite.assets} != {
            UUID(p["batch_id"]): p for p in lineage["satellite_assets"]
        }:
            raise ValueError("Evidence satellite checksums differ from features")
        weather = lineage["weather"]
        weather_series = WeatherSeries(
            records=sorted(self.weather.series.records, key=lambda row: row.valid_at),
            quality_flags=self.weather.series.quality_flags,
        )
        if (
            self.weather.cell_key,
            self.weather.product,
            self.weather.model,
            self.weather.records_sha256,
        ) != (
            weather["cell_key"],
            weather["product"],
            weather["model"],
            weather["records_sha256"],
        ) or digest(weather_series.model_dump(mode="json")) != self.weather.records_sha256:
            raise ValueError("Evidence weather differs from frozen inputs")
        if len({p.asset_id for p in self.weather.assets}) != len(self.weather.assets) or {
            p.asset_id: p.sha256 for p in self.weather.assets
        } != {UUID(p["asset_id"]): p["sha256"] for p in weather["assets"]}:
            raise ValueError("Evidence weather source checksums differ from features")
        config = FeatureSpec.model_validate(lineage["config"])
        actual_weather = weather_at_cutoff(
            self.weather.series, self.as_of, max_age_hours=config.weather_max_age_hours
        )
        if (
            self.weather.valid_at
            != (actual_weather.record.valid_at if actual_weather.record else None)
            or self.weather.precipitation_interval_end != actual_weather.precipitation_interval_end
        ):
            raise ValueError("Evidence weather times differ from feature selection")
        if any(
            r.valid_at > self.as_of or r.retrieved_at > self.retrieval_cutoff
            for r in self.weather.series.records
        ):
            raise ValueError("Evidence weather exceeds its frozen cutoff")
        assets = {p.asset_id: p.sha256 for p in self.source_checksums}
        required: list[AssetPin] = list(self.satellite.assets) + self.weather.assets
        if self.baseline_asset:
            required.append(self.baseline_asset)
        if (
            len(assets) != len(self.source_checksums)
            or assets != {p.asset_id: p.sha256 for p in required}
            or any(assets[p.asset_id] != p.sha256 for p in required)
        ):
            raise ValueError("Evidence source checksums conflict or are incomplete")
        baseline = lineage["baseline"]
        if self.baseline is None:
            if baseline is not None or self.baseline_asset is not None or self.baseline_version:
                raise ValueError("Evidence must preserve a missing baseline")
        elif (
            baseline is None
            or self.baseline_asset is None
            or self.baseline_version != self.baseline.version
            or digest(self.baseline.model_dump(mode="json")) != baseline["value_sha256"]
            or self.baseline_asset.model_dump(mode="json") != baseline["asset"]
        ):
            raise ValueError("Evidence baseline differs from frozen input")
        if (self.model_id is None) != (self.model is None):
            raise ValueError("Evidence supervised model identity is incomplete")
        if self.model and (
            self.model.model_id != self.model_id
            or self.model.scorer_version != self.scorer_version
            or self.explanation.get("inference_manifest_sha256")
            != self.model.inference_manifest_sha256
            or self.explanation.get("approval_reference") != self.model.approval_reference
        ):
            raise ValueError("Evidence model/scorer approval mismatch")
        # Reuse score contract: model/probability rules cannot diverge at evidence time.
        ScoreResult(
            run_id=self.run_id,
            snapshot_id=self.snapshot_id,
            mode=self.mode,
            as_of=self.as_of,
            feature_version=self.feature_version,
            scorer_version=self.scorer_version,
            model_id=self.model_id,
            score_kind=self.score_kind,
            priority_score=self.priority_score,
            escalation_probability=self.escalation_probability,
            priority=self.priority,
            reasons=self.reasons,
            explanation=self.explanation,
            scored_at=self.scored_at,
        )
        return self


def build_evidence(
    *,
    score_id: UUID,
    score: ScoreResult,
    snapshot: ClusterSnapshot,
    feature: FeatureSet,
    region: EvidenceRegion,
    firms: dict[str, Any],
    pins: EnrichmentPins,
    run_config_hash: str,
    feature_stage_hash: str,
    warnings: list[str],
    approved: ApprovedScorer | None = None,
) -> EvidencePayload:
    """Build only from already frozen inputs; never refetch or select newer revisions."""
    feature = validate_feature_set(feature)
    lineage: dict[str, Any] = feature.lineage
    weather = lineage["weather"]
    end = feature.as_of.replace(minute=0, second=0, microsecond=0)
    series = pins.weather[weather["cell_key"]]
    series = WeatherSeries(
        records=sorted(
            [r for r in series.records if end - timedelta(hours=23) <= r.valid_at <= end],
            key=lambda r: r.valid_at,
        ),
        quality_flags=series.quality_flags,
    )
    baseline = lineage["baseline"]
    climate = pins.baselines[baseline["cell_key"]] if baseline else None
    source_pins = [SatelliteAssetPin.model_validate(p) for p in lineage["satellite_assets"]]
    weather_pins = [AssetPin.model_validate(p) for p in weather["assets"]]
    if {p.batch_id: p for p in pins.satellite_assets} != {p.batch_id: p for p in source_pins}:
        raise ValueError("Evidence satellite pins differ from frozen enrichment")
    pinned_assets = {p.asset_id: p.sha256 for p in pins.assets}
    if len(pinned_assets) != len(pins.assets):
        raise ValueError("Evidence enrichment pins contain duplicate assets")
    baseline_pin = AssetPin.model_validate(baseline["asset"]) if baseline else None
    all_pins: list[AssetPin] = list(source_pins) + weather_pins
    if baseline_pin:
        all_pins.append(baseline_pin)
    if any(
        pinned_assets.get(p.asset_id) != p.sha256
        for p in weather_pins + ([baseline_pin] if baseline_pin else [])
    ):
        raise ValueError("Evidence enrichment checksums differ from features")
    checksums: dict[UUID, str] = {}
    for pin in all_pins:
        if pin.asset_id in checksums and checksums[pin.asset_id] != pin.sha256:
            raise ValueError("Evidence source checksum conflict")
        checksums[pin.asset_id] = pin.sha256
    model = (
        None
        if approved is None
        else ModelEvidence(
            model_id=approved.model_id,
            scorer_version=approved.scorer_version,
            inference_manifest_sha256=approved.manifest_sha256,
            approval_reference=approved.approval_reference,
        )
    )
    return EvidencePayload(
        alert_id=uuid5(score_id, "evidence-v1"),
        score_id=score_id,
        **score.model_dump(exclude={"schema_version", "quality_flags", "warnings"}),
        region=region,
        snapshot=snapshot,
        feature=feature,
        feature_hash=feature.feature_hash,
        feature_payload_json=canonical_bytes(
            feature.model_dump(mode="json", exclude={"feature_hash"})
        ).decode(),
        satellite=SatelliteEvidence(
            **firms, assets=source_pins, snapshot_input_hash=lineage["snapshot_input_hash"]
        ),
        weather=WeatherEvidence(
            **{
                k: weather[k]
                for k in (
                    "cell_key",
                    "product",
                    "model",
                    "valid_at",
                    "precipitation_interval_end",
                    "records_sha256",
                )
            },
            series=series,
            assets=weather_pins,
        ),
        baseline=climate,
        baseline_version=climate.version if climate else None,
        baseline_asset=baseline_pin,
        model=model,
        source_checksums=[AssetPin(asset_id=i, sha256=h) for i, h in checksums.items()],
        retrieval_cutoff=pins.retrieval_cutoff,
        run_config_hash=run_config_hash,
        feature_stage_hash=feature_stage_hash,
        warnings=sorted(set(warnings + score.warnings + score.quality_flags)),
    )


def evidence_content(payload: EvidencePayload) -> tuple[bytes, str]:
    checked = EvidencePayload.model_validate(payload.model_dump())
    content = serialize_evidence(checked.model_dump(mode="python"))
    return content, evidence_hash(content)


def wire_schema() -> dict[str, Any]:
    """Describe saved decimal strings, rather than the builder's Python floats."""
    from firewatch.evidence.serialization import DECIMAL_PATTERN

    schema = EvidencePayload.model_json_schema()

    def convert(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "number":
                for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
                    node.pop(key, None)
                node.update(type="string", pattern=DECIMAL_PATTERN)
            for child in node.values():
                convert(child)
        elif isinstance(node, list):
            for child in node:
                convert(child)

    convert(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["required"] = ["schema_version", "canonicalization_version"] + schema["required"]
    schema["$id"] = "urn:firewatch:evidence-v1:evidence-json-v1"
    schema["description"] = "Saved wire format; canonical-evidence.md specifies encoding rules."
    return schema
