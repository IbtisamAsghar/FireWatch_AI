"""Portable, checksummed offline replay inputs; no provider clients or serialized code."""

import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast
from uuid import UUID

import sqlalchemy as sa
from pydantic import Field
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firewatch.db.models import (
    ClimateBaselineRecord,
    Detection,
    DetectionRegion,
    DetectionSource,
    IngestionBatch,
    Region,
    Run,
    SourceAsset,
    WeatherRecord,
)
from firewatch.evidence.canonical import canonical_bytes
from firewatch.processing.clusters import ClusteringSpec, SnapshotBatch
from firewatch.processing.features import FeatureSpec
from firewatch.repositories.features import EnrichmentPins, collect_enrichment
from firewatch.repositories.revisions import RevisionPolicy
from firewatch.schemas.common import DTO, Digest, UTCDateTime
from firewatch.schemas.operator import ReplayScenario
from firewatch.sources.firms_handoff import scan_secrets

MODELS = (
    Region,
    SourceAsset,
    IngestionBatch,
    Detection,
    DetectionSource,
    DetectionRegion,
    WeatherRecord,
    ClimateBaselineRecord,
)
TABLES = {m.__tablename__: cast(sa.Table, m.__table__) for m in MODELS}
MAX_BYTES = 256 * 1024 * 1024


class BundleFile(DTO):
    path: str = Field(min_length=1, max_length=180)
    sha256: Digest
    size: int = Field(ge=0, le=64 * 1024 * 1024)


class ReplayBundle(DTO):
    schema_version: Literal["replay-bundle-v1"] = "replay-bundle-v1"
    data_kind: Literal["real", "synthetic"]
    scenario_kind: Literal["active", "quiet", "degraded"]
    coverage_note: str = Field(min_length=1, max_length=2000)
    scenario: ReplayScenario
    feature_config: FeatureSpec
    clustering_config: ClusteringSpec
    policy: RevisionPolicy
    pins: EnrichmentPins
    prepared_at: UTCDateTime
    files: list[BundleFile] = Field(min_length=1, max_length=512)
    numeric_tolerance: float = Field(default=1e-12, ge=1e-12, le=1e-12)
    detection_max_age_hours: float = Field(default=24, gt=0, le=168)


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    raise TypeError("Unsupported canonical bundle value")


def _content(value: Any) -> bytes:
    return json.dumps(
        value,
        default=_encode,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()


def _file(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if (
        parts.is_absolute()
        or ".." in parts.parts
        or "\\" in relative
        or ":" in relative
        or str(parts) != relative
    ):
        raise ValueError("Unsafe replay bundle path")
    result = root.joinpath(*parts.parts)
    if not result.resolve().is_relative_to(root.resolve()) or result.is_symlink():
        raise ValueError("Replay bundle path escapes its directory")
    return result


def _read(path: Path, maximum: int) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(maximum + 1)
    if len(content) > maximum:
        raise ValueError("Replay input exceeds size bound")
    return content


def _pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate replay JSON key")
        result[key] = value
    return result


def _json(content: bytes) -> Any:
    return json.loads(
        content,
        object_pairs_hook=_pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")),
    )


def load_bundle(root: Path, expected_hash: str) -> ReplayBundle:
    raw = _read(_file(root, "manifest.json"), 2 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("Replay bundle manifest checksum mismatch")
    bundle = ReplayBundle.model_validate(_json(raw))
    paths = [f.path for f in bundle.files]
    if len(paths) != len(set(paths)) or "canonical.json" not in paths:
        raise ValueError("Replay bundle file inventory is invalid")
    total = 0
    for file in bundle.files:
        total += file.size
        if total > MAX_BYTES:
            raise ValueError("Replay bundle exceeds total size bound")
        content = _read(_file(root, file.path), min(file.size, 64 * 1024 * 1024))
        if len(content) != file.size or hashlib.sha256(content).hexdigest() != file.sha256:
            raise ValueError("Replay bundle file checksum mismatch")
    if (
        bundle.scenario.bundle_sha256 is not None
        or bundle.scenario.window_end > bundle.prepared_at
        or bundle.policy.retrieval_cutoff != bundle.pins.retrieval_cutoff
        or bundle.scenario.feature_version != bundle.feature_config.feature_version
    ):
        raise ValueError("Replay bundle versions or cutoff disagree")
    return bundle


def _rows(session: Session, model: Any, condition: Any) -> list[dict[str, Any]]:
    rows = list(
        session.execute(sa.select(model.__table__).where(condition).limit(50001)).mappings()
    )
    if len(rows) > 50000:
        raise ValueError("Replay canonical row bound exceeded")
    return sorted([dict(r) for r in rows], key=lambda r: _content(r))


def export_bundle(
    session: Session,
    run: Run,
    snapshots: SnapshotBatch,
    policy: RevisionPolicy,
    config: FeatureSpec,
    scenario: ReplayScenario,
    output: Path,
    *,
    prepared_at: datetime,
    data_kind: Literal["real", "synthetic"],
    scenario_kind: Literal["active", "quiet", "degraded"],
    coverage_note: str,
    secrets: list[str] | None = None,
) -> str:
    """Read snapshot inputs; export exact source UUIDs, raw bytes, revisions and enrichment."""
    if output.exists():
        raise ValueError("Replay export requires a new output directory")
    if run.as_of != scenario.window_end or run.id != snapshots.run_id or snapshots.mode != "replay":
        raise ValueError("Replay export cutoff disagrees with scenario")
    pins = collect_enrichment(session, run, snapshots, policy, config, prepared_at)
    # Availability must be the same for satellite selection and cached enrichment.
    policy = policy.model_copy(update={"retrieval_cutoff": pins.retrieval_cutoff})
    batch_ids = set(policy.batch_ids)
    sources = _rows(session, DetectionSource, DetectionSource.batch_id.in_(batch_ids))
    detection_ids = {r["detection_id"] for r in sources}
    detections = _rows(session, Detection, Detection.id.in_(detection_ids))
    batch_ids.update(r["primary_batch_id"] for r in detections)
    batches = _rows(session, IngestionBatch, IngestionBatch.id.in_(batch_ids))
    if any(b["region_id"] != run.region_id for b in batches):
        raise ValueError("Replay export cannot include another region's primary batch")
    asset_ids = {b["asset_id"] for b in batches} | {p.asset_id for p in pins.assets}
    baseline_ids = {p.asset_id for p in pins.baselines.values() if p is not None}
    baselines = _rows(
        session, ClimateBaselineRecord, ClimateBaselineRecord.asset_id.in_(baseline_ids)
    )
    for baseline in baselines:
        asset_ids.update(UUID(i) for i in baseline["statistics"].get("source_assets", []))
    weather_ids = {r.asset_id for w in pins.weather.values() for r in w.records}
    rows = {
        "regions": _rows(session, Region, Region.id == run.region_id),
        "source_assets": _rows(session, SourceAsset, SourceAsset.id.in_(asset_ids)),
        "ingestion_batches": batches,
        "detections": detections,
        "detection_sources": sources,
        "detection_regions": _rows(
            session,
            DetectionRegion,
            (DetectionRegion.region_id == run.region_id)
            & DetectionRegion.detection_id.in_(detection_ids),
        ),
        "weather_records": _rows(session, WeatherRecord, WeatherRecord.asset_id.in_(weather_ids)),
        "climate_baselines": baselines,
    }
    if {r["id"] for r in rows["source_assets"]} != asset_ids:
        raise ValueError("Replay source dependency is unavailable")
    output.mkdir(parents=True)
    inventory: dict[str, BundleFile] = {}
    for asset in rows["source_assets"]:
        raw = _read(Path(asset["storage_path"]), 64 * 1024 * 1024)
        sha = hashlib.sha256(raw).hexdigest()
        if sha != asset["content_hash"]:
            raise ValueError("Replay source evidence checksum mismatch")
        name = "raw/" + sha + ".bin"
        target = _file(output, name)
        target.parent.mkdir(exist_ok=True)
        if not target.exists():
            target.write_bytes(raw)
        inventory[name] = BundleFile(path=name, sha256=sha, size=len(raw))
        asset["storage_path"] = name
    for region in rows["regions"]:
        region["enabled"] = False  # Historical import must never enable live scheduling.
    canonical = _content(rows)
    (output / "canonical.json").write_bytes(canonical)
    inventory["canonical.json"] = BundleFile(
        path="canonical.json", sha256=hashlib.sha256(canonical).hexdigest(), size=len(canonical)
    )
    bundle = ReplayBundle(
        data_kind=data_kind,
        scenario_kind=scenario_kind,
        coverage_note=coverage_note,
        scenario=scenario.model_copy(update={"bundle_sha256": None}),
        feature_config=config,
        clustering_config=snapshots.config,
        policy=policy,
        pins=pins,
        prepared_at=prepared_at,
        files=sorted(inventory.values(), key=lambda f: f.path),
    )
    content = canonical_bytes(bundle.model_dump(mode="json"))
    temporary = output / "manifest.pending"
    temporary.write_bytes(content)
    scan_secrets([temporary, *(_file(output, f.path) for f in bundle.files)], secrets or [])
    total = sum(f.size for f in bundle.files)
    if total > MAX_BYTES:
        raise ValueError("Replay bundle exceeds total size bound")
    temporary.replace(output / "manifest.json")
    digest = hashlib.sha256(content).hexdigest()
    load_bundle(output, digest)
    return digest


def _hydrate(table: sa.Table, row: dict[str, Any]) -> dict[str, Any]:
    if set(row) != {c.name for c in table.columns}:
        raise ValueError("Replay canonical columns do not match the database contract")
    result = dict(row)
    for column in table.columns:
        value = result[column.name]
        if value is not None and isinstance(column.type, sa.Uuid):
            result[column.name] = UUID(value)
        elif value is not None and isinstance(column.type, sa.DateTime):
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                raise ValueError("Replay timestamps must be aware")
            result[column.name] = stamp
    return result


def import_bundle(session: Session, root: Path, bundle: ReplayBundle) -> None:
    """Caller owns one atomic transaction. Existing identities must match; never enable a region."""
    expected = hashlib.sha256(canonical_bytes(bundle.model_dump(mode="json"))).hexdigest()
    if load_bundle(root, expected) != bundle:
        raise ValueError("Replay bundle changed after verification")
    data = _json(_read(root / "canonical.json", 64 * 1024 * 1024))
    if not isinstance(data, dict) or set(data) != set(TABLES):
        raise ValueError("Replay canonical table inventory mismatch")
    session.execute(sa.select(sa.func.pg_advisory_xact_lock(0x46575245504C)))
    inventory = {f.path: f for f in bundle.files}
    for name, table in TABLES.items():
        records = data[name]
        if not isinstance(records, list) or len(records) > 50000:
            raise ValueError("Replay canonical row bound exceeded")
        for serialized in records:
            row = _hydrate(table, serialized)
            if name == "regions" and row["enabled"] is not False:
                raise ValueError("Replay imports cannot enable live scheduling")
            if name == "source_assets":
                path = row["storage_path"]
                if path not in inventory or inventory[path].sha256 != row["content_hash"]:
                    raise ValueError("Replay source does not reference verified raw evidence")
                row["storage_path"] = str(_file(root, path).resolve())
            condition = sa.and_(*(table.c[c.name] == row[c.name] for c in table.primary_key))
            existing = session.execute(sa.select(table).where(condition)).mappings().one_or_none()
            if existing is not None:
                for key, value in row.items():
                    if name == "regions" and key == "enabled":
                        continue
                    if name == "source_assets" and key == "storage_path":
                        raw = _read(Path(existing[key]), 64 * 1024 * 1024)
                        if hashlib.sha256(raw).hexdigest() != row["content_hash"]:
                            raise ValueError("Existing replay raw source differs")
                        continue
                    if existing[key] != value:
                        raise ValueError("Replay canonical identity conflicts with saved data")
            else:
                session.execute(insert(table).values(**row))
