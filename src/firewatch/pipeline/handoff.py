"""Verified Member 3 Parquet features and Member 1 run/status handoff."""

import hashlib
import json
import platform
import re
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from firewatch.api.presentation import SENSITIVE_KEYS, run_view, stages
from firewatch.config import Settings
from firewatch.db.models import Job, Region, Run
from firewatch.evidence.canonical import canonical_bytes
from firewatch.processing.clusters import SnapshotBatch
from firewatch.processing.features import (
    FEATURE_NAMES,
    MISSING_NAMES,
    MODEL_INPUT_NAMES,
    UNITS,
    FeatureSpec,
    model_inputs,
    validate_feature_set,
)
from firewatch.repositories.features import build_run_features
from firewatch.repositories.revisions import RevisionPolicy, freeze_firms_inputs
from firewatch.schemas.api import JobView, RunView
from firewatch.schemas.common import RunStatus, Stage, StageStatus
from firewatch.schemas.contracts import FeatureSet
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import utc

FILES = {
    "features.parquet",
    "memberships.parquet",
    "schema.json",
    "provenance.json",
    "runs.json",
    "jobs.json",
    "README.md",
}
MAX_ROWS = 100000
MAX_MEMBERS = 200000


def feature_schema() -> Any:
    fields = [
        pa.field(n, pa.string(), nullable=False)
        for n in ("run_id", "snapshot_id", "region_id", "mode", "feature_version", "feature_hash")
    ]
    fields += [
        pa.field(n, pa.timestamp("us", tz="UTC"), nullable=False)
        for n in ("as_of", "latest_detection_at", "processed_at")
    ]
    fields += [
        pa.field("latitude", pa.float64(), nullable=False),
        pa.field("longitude", pa.float64(), nullable=False),
        pa.field("member_count", pa.int64(), nullable=False),
        pa.field("lineage_json", pa.string(), nullable=False),
    ]
    fields += [
        pa.field(
            n,
            pa.int64() if n.startswith("detections_") else pa.float64(),
            nullable=n in MISSING_NAMES,
            metadata={"unit": UNITS[n]},
        )
        for n in FEATURE_NAMES
    ]
    fields += [pa.field(n + "_missing", pa.bool_(), nullable=False) for n in MISSING_NAMES]
    return pa.schema(
        fields, metadata={"schema_version": "feature-handoff-v1", "feature_version": "features-v1"}
    )


def membership_schema() -> Any:
    fields = [
        pa.field(n, pa.string(), nullable=False)
        for n in ("run_id", "snapshot_id", "detection_id", "batch_id", "revision_hash")
    ]
    fields += [pa.field("acquired_at", pa.timestamp("us", tz="UTC"), nullable=False)]
    return pa.schema(fields, metadata={"schema_version": "membership-handoff-v1"})


def schema_document() -> dict[str, Any]:
    return dict(
        schema_version="pipeline-handoff-schema-v1",
        feature_version="features-v1",
        model_input_names=list(MODEL_INPUT_NAMES),
        units=UNITS,
        fields=[
            dict(
                name=f.name,
                type=str(f.type),
                nullable=f.nullable,
                role="model_input" if f.name in MODEL_INPUT_NAMES else "metadata",
            )
            for f in feature_schema()
        ],
        membership_fields=[
            dict(name=f.name, type=str(f.type), nullable=f.nullable) for f in membership_schema()
        ],
        label_policy=(
            "No labels, predictions, event groups or split assignments in feature columns."
        ),
        timestamp_policy="UTC microsecond timestamps; acquisition/cutoff separate from processing.",
    )


def _safe(value: Any, secrets: list[str], depth: int = 0) -> None:
    if depth > 32:
        raise ValueError("Handoff metadata exceeds nesting bound")
    if isinstance(value, str):
        if (
            value.startswith(("/", "\\"))
            or re.search(
                r"(?i)(?:https?://|postgres(?:ql)?(?:\+psycopg)?://|file://|[a-z]:\\)", value
            )
            or any(secret and len(secret) >= 8 and secret in value for secret in secrets)
        ):
            raise ValueError("Handoff contains private paths, URLs or configured secrets")
    elif isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower().replace("-", "_") in SENSITIVE_KEYS:
                raise ValueError("Handoff contains private metadata keys")
            _safe(item, secrets, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _safe(item, secrets, depth + 1)


def _json(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value) + b"\n")


def _collect(
    engine: Engine, settings: Settings, run_ids: list[UUID], now: datetime
) -> dict[str, Any]:
    features, memberships, provenance, runs, jobs = [], [], [], [], []
    with Session(engine) as session, session.begin():
        for identifier in sorted(run_ids, key=str):
            run = session.get(Run, identifier)
            if run is None or run.status != RunStatus.COMPLETED:
                raise ValueError("Handoff requires existing completed runs")
            area = session.get(Region, run.region_id)
            assert area is not None
            manifest = run.input_asset_manifest
            config = FeatureSpec.model_validate(manifest["features"]["config"])
            computed = build_run_features(session, run.id, config)
            batch = SnapshotBatch.model_validate(manifest["clustering"]["batch"])
            policy = RevisionPolicy.model_validate(manifest["firms"]["policy"])
            selected = {
                str(r.detection_id): r
                for r in freeze_firms_inputs(
                    session, run.id, run.as_of - timedelta(hours=24), policy
                )
            }
            lookup = {str(d.snapshot.snapshot_id): d.snapshot for d in batch.snapshots}
            for feature in computed:
                snapshot = lookup[str(feature.snapshot_id)]
                features.append(
                    dict(
                        run_id=str(run.id),
                        snapshot_id=str(feature.snapshot_id),
                        region_id=str(run.region_id),
                        mode=run.mode.value,
                        as_of=run.as_of,
                        latest_detection_at=snapshot.latest_detection_at,
                        processed_at=run.completed_at,
                        latitude=snapshot.latitude,
                        longitude=snapshot.longitude,
                        member_count=len(snapshot.member_ids),
                        feature_version=feature.feature_version,
                        feature_hash=feature.feature_hash,
                        lineage_json=canonical_bytes(feature.lineage).decode(),
                        **model_inputs(feature),
                    )
                )
                for member in snapshot.member_ids:
                    source = selected[str(member)]
                    memberships.append(
                        dict(
                            run_id=str(run.id),
                            snapshot_id=str(snapshot.snapshot_id),
                            detection_id=str(member),
                            batch_id=str(source.batch_id),
                            revision_hash=source.revision_hash,
                            acquired_at=source.observation.acquired_at,
                        )
                    )
            runs.append(run_view(session, run, now, settings).model_dump(mode="json"))
            for job in session.scalars(
                select(Job).where(Job.result_run_id == run.id).order_by(Job.id).limit(1001)
            ):
                jobs.append(
                    JobView(
                        job_id=job.id,
                        type=job.type,
                        status=job.status,
                        attempts=job.attempts,
                        available_at=job.available_at,
                        lease_until=job.lease_until,
                        heartbeat_at=job.heartbeat_at,
                        result_run_id=job.result_run_id,
                        stages=stages(run),
                        failure="Job execution failed" if job.error else None,
                    ).model_dump(mode="json")
                )
            provenance.append(
                dict(
                    run_id=str(run.id),
                    region_id=str(run.region_id),
                    config_hash=run.config_hash,
                    region_name=area.name,
                    stage_manifests={k: manifest[k] for k in ("firms", "clustering", "features")},
                    scenario_id=manifest.get("orchestration", {})
                    .get("request", {})
                    .get("scenario_id"),
                    bundle_sha256=manifest.get("orchestration", {})
                    .get("request", {})
                    .get("bundle_sha256"),
                )
            )
            if len(features) > MAX_ROWS or len(memberships) > MAX_MEMBERS or len(jobs) > 1000:
                raise ValueError("Handoff row bound exceeded")
    return dict(
        features=features, memberships=memberships, provenance=provenance, runs=runs, jobs=jobs
    )


def _table(path: Path, schema: Any, max_rows: int) -> Any:
    if path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Invalid Parquet file size/path")
    file = pq.ParquetFile(path)
    if file.metadata.num_rows > max_rows or not file.schema_arrow.equals(
        schema, check_metadata=True
    ):
        raise ValueError("Parquet schema or row bound mismatch")
    uncompressed = sum(
        file.metadata.row_group(i).column(j).total_uncompressed_size
        for i in range(file.metadata.num_row_groups)
        for j in range(file.metadata.num_columns)
    )
    if uncompressed > 128 * 1024 * 1024:
        raise ValueError("Parquet uncompressed size bound exceeded")
    table = file.read()
    table.validate(full=True)
    return table


def verify_handoff(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    """Verify file inventory, Arrow types, feature hashes, memberships and public DTOs."""
    path = root / "manifest.json"
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid handoff manifest")
    raw = path.read_bytes()
    manifest_hash = hashlib.sha256(raw).hexdigest()
    if expected_hash is not None and manifest_hash != expected_hash:
        raise ValueError("Handoff manifest checksum mismatch")
    manifest = json.loads(raw)
    if (
        manifest.get("schema_version") != "pipeline-handoff-v1"
        or set(manifest["files"]) != FILES
        or manifest.get("feature_version") != "features-v1"
        or manifest.get("model_input_names") != list(MODEL_INPUT_NAMES)
        or manifest.get("data_kind") not in {"real", "synthetic", "unclassified"}
        or manifest.get("labels_included") is not False
        or manifest.get("training_ready") is not False
    ):
        raise ValueError("Handoff manifest contract mismatch")
    for name, entry in manifest["files"].items():
        item = root / name
        if item.is_symlink() or item.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Invalid handoff file")
        content = item.read_bytes()
        if len(content) != entry["size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise ValueError("Handoff file checksum mismatch")
    schema = json.loads((root / "schema.json").read_bytes())
    if schema != schema_document():
        raise ValueError("Handoff schema/model whitelist changed")
    feature_rows = _table(root / "features.parquet", feature_schema(), MAX_ROWS).to_pylist()
    member_rows = _table(root / "memberships.parquet", membership_schema(), MAX_MEMBERS).to_pylist()
    run_rows = [RunView.model_validate(r) for r in json.loads((root / "runs.json").read_bytes())]
    job_rows = [JobView.model_validate(r) for r in json.loads((root / "jobs.json").read_bytes())]
    run_lookup = {str(r.run_id): r for r in run_rows}
    if (
        len(run_lookup) != len(run_rows)
        or set(run_lookup) != set(manifest["run_ids"])
        or any(r.status != RunStatus.COMPLETED for r in run_rows)
        or any(
            any(r.stages.get(stage) != StageStatus.SUCCEEDED for stage in Stage) for r in run_rows
        )
        or any(str(j.result_run_id) not in run_lookup for j in job_rows)
    ):
        raise ValueError("Handoff run/status linkage mismatch")
    provenance = json.loads((root / "provenance.json").read_bytes())
    if len(provenance) != len(run_lookup) or {entry["run_id"] for entry in provenance} != set(
        run_lookup
    ):
        raise ValueError("Handoff provenance must cover every run exactly once")
    snapshot_lookup = {}
    revision_lookup = {}
    expected_hashes: dict[tuple[str, str], str] = {}
    for entry in provenance:
        run_id = entry["run_id"]
        if run_id not in run_lookup or entry["config_hash"] != run_lookup[run_id].config_hash:
            raise ValueError("Handoff provenance/run mismatch")
        clustering = entry["stage_manifests"]["clustering"]
        if digest(clustering["batch"]) != clustering["sha256"]:
            raise ValueError("Handoff snapshot manifest mismatch")
        batch = SnapshotBatch.model_validate(clustering["batch"])
        if (
            str(batch.run_id) != run_id
            or batch.as_of != run_lookup[run_id].as_of
            or batch.mode != run_lookup[run_id].mode
        ):
            raise ValueError("Handoff snapshot/run identity mismatch")
        feature_stage = entry["stage_manifests"]["features"]
        if (
            digest({k: v for k, v in feature_stage.items() if k != "sha256"})
            != feature_stage["sha256"]
        ):
            raise ValueError("Handoff feature manifest mismatch")
        expected_hashes.update(
            {(run_id, key): value for key, value in feature_stage["feature_hashes"].items()}
        )
        for detail in batch.snapshots:
            snapshot_lookup[(run_id, str(detail.snapshot.snapshot_id))] = detail.snapshot
        revision_lookup[run_id] = {
            r["detection_id"]: r for r in entry["stage_manifests"]["firms"]["selected"]
        }
    seen = set()
    for row in feature_rows:
        key = (row["run_id"], row["snapshot_id"])
        if (
            key in seen
            or key not in snapshot_lookup
            or expected_hashes.get(key) != row["feature_hash"]
        ):
            raise ValueError("Handoff feature snapshot join mismatch")
        seen.add(key)
        run = run_lookup[row["run_id"]]
        snapshot = snapshot_lookup[key]
        if (
            row["region_id"] != str(run.region_id)
            or row["mode"] != run.mode.value
            or row["as_of"] != run.as_of
            or row["processed_at"] != run.completed_at
            or row["latest_detection_at"] != snapshot.latest_detection_at
            or row["latitude"] != snapshot.latitude
            or row["longitude"] != snapshot.longitude
            or row["member_count"] != len(snapshot.member_ids)
        ):
            raise ValueError("Handoff feature metadata mismatch")
        validate_feature_set(
            FeatureSet(
                run_id=row["run_id"],
                snapshot_id=row["snapshot_id"],
                mode=row["mode"],
                as_of=row["as_of"],
                feature_version=row["feature_version"],
                values={n: row[n] for n in FEATURE_NAMES},
                missing_flags={n: row[n + "_missing"] for n in MISSING_NAMES},
                units=UNITS,
                lineage=json.loads(row["lineage_json"]),
                feature_hash=row["feature_hash"],
            )
        )
    if seen != set(snapshot_lookup):
        raise ValueError("Handoff snapshot/feature cardinality mismatch")
    actual = set()
    for row in member_rows:
        key = (row["run_id"], row["snapshot_id"])
        member_snapshot = snapshot_lookup.get(key)
        triple = (*key, row["detection_id"])
        revision = revision_lookup.get(row["run_id"], {}).get(row["detection_id"])
        if (
            member_snapshot is None
            or revision is None
            or triple in actual
            or row["detection_id"] not in {str(i) for i in member_snapshot.member_ids}
            or row["batch_id"] != revision["batch_id"]
            or row["revision_hash"] != revision["revision_hash"]
            or not member_snapshot.as_of - timedelta(hours=6)
            < row["acquired_at"]
            <= member_snapshot.as_of
        ):
            raise ValueError("Handoff member/source/time linkage mismatch")
        actual.add(triple)
    expected = {
        (run_id, sid, str(member))
        for (run_id, sid), snap in snapshot_lookup.items()
        for member in snap.member_ids
    }
    if actual != expected or manifest["row_counts"] != {
        "features": len(feature_rows),
        "memberships": len(member_rows),
        "runs": len(run_rows),
        "jobs": len(job_rows),
    }:
        raise ValueError("Handoff row count mismatch")
    return dict(
        manifest_sha256=manifest_hash,
        **manifest["row_counts"],
        verified=True,
        feature_hashes=[r["feature_hash"] for r in feature_rows],
    )


def export_handoff(
    engine: Engine,
    settings: Settings,
    run_ids: list[UUID],
    output: Path,
    *,
    data_kind: Literal["real", "synthetic", "unclassified"] = "unclassified",
    now: datetime | None = None,
) -> dict[str, Any]:
    if not run_ids or len(run_ids) > 100 or len(set(run_ids)) != len(run_ids):
        raise ValueError("Choose 1-100 unique completed run IDs")
    target = output.resolve()
    if target.exists():
        raise ValueError("Handoff output already exists; choose a new directory")
    clock = utc(now or datetime.now(UTC))
    collected = _collect(engine, settings, run_ids, clock)
    secrets = [
        settings.admin_api_token.get_secret_value(),
        settings.database_url.get_secret_value(),
        make_url(settings.database_url.get_secret_value()).password or "",
    ]
    for value in (settings.firms_map_key, settings.chain_private_key):
        if value:
            secrets.append(value.get_secret_value())
    # Validate plaintext before Parquet compression, and publish only after round-trip verification.
    _safe(json.loads(json.dumps(collected, default=lambda v: v.isoformat())), secrets)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pipeline-handoff-", dir=target.parent)).resolve()
    if staging.parent != target.parent or not staging.name.startswith(".pipeline-handoff-"):
        raise ValueError("Invalid handoff staging path")
    try:
        pq.write_table(
            pa.Table.from_pylist(collected["features"], schema=feature_schema()),
            staging / "features.parquet",
            version="2.6",
            compression="zstd",
            row_group_size=4096,
        )
        pq.write_table(
            pa.Table.from_pylist(collected["memberships"], schema=membership_schema()),
            staging / "memberships.parquet",
            version="2.6",
            compression="zstd",
            row_group_size=4096,
        )
        _json(staging / "schema.json", schema_document())
        for key in ("provenance", "runs", "jobs"):
            _json(staging / (key + ".json"), collected[key])
        (staging / "README.md").write_text(
            "# Member 3 pipeline handoff\n\nMember 4: read features.parquet using schema.json. "
            "Only model_input_names are model inputs; metadata and lineage are excluded. "
            "Nulls and missing flags are preserved. No labels/splits/model skill are supplied.\n\n"
            "Member 1: runs.json/jobs.json use existing API DTOs with historical and processing "
            "times. memberships.parquet and provenance.json bind snapshots to source revisions. "
            "Verify every file against manifest.json before use. SHA-256 is tamper detection, "
            "not an authenticity signature. Data label: " + data_kind + ".\n",
            encoding="utf-8",
        )
        inventory = {
            n: dict(
                sha256=hashlib.sha256((staging / n).read_bytes()).hexdigest(),
                size=(staging / n).stat().st_size,
            )
            for n in sorted(FILES)
        }
        manifest = dict(
            schema_version="pipeline-handoff-v1",
            feature_version="features-v1",
            data_kind=data_kind,
            created_at=clock.isoformat(),
            model_input_names=list(MODEL_INPUT_NAMES),
            run_ids=sorted(str(i) for i in run_ids),
            row_counts={k: len(collected[k]) for k in ("features", "memberships", "runs", "jobs")},
            files=inventory,
            package_version=version("firewatch-ai"),
            python_version=platform.python_version(),
            pyarrow_version=pa.__version__,
            labels_included=False,
            training_ready=False,
        )
        _json(staging / "manifest.json", manifest)
        result = verify_handoff(staging)
        staging.rename(target)
        return result
    finally:
        if staging.exists():
            if staging.parent != target.parent or not staging.name.startswith(".pipeline-handoff-"):
                raise ValueError("Refusing cleanup outside handoff staging directory")
            shutil.rmtree(staging)
