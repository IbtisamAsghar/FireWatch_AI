"""Bounded commits with database checkpoints and complete-day archive publication."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from firewatch.db.models import DetectionSource, IngestionBatch, Region, SourceAsset
from firewatch.repositories.detections import save_detection_result
from firewatch.schemas.common import JobStatus, Provider
from firewatch.schemas.contracts import DetectionInput
from firewatch.sources.archive_manifest import ARCHIVE_VERSION, ArchiveError
from firewatch.sources.archive_stage import PreparedArchive, atomic_json, hash_file
from firewatch.sources.firms_normalize import NORMALIZER_VERSION, RowRevision, digest


def _registry(session: Session, prepared: PreparedArchive, region_id: UUID) -> SourceAsset:
    manifest = prepared.manifest
    metadata = {
        "archive_manifest": manifest.model_dump(mode="json"),
        "normalization_policy": prepared.policy.model_dump(mode="json"),
        "region_id": str(region_id),
    }
    session.execute(
        insert(SourceAsset)
        .values(
            id=prepared.import_id,
            provider=Provider.FIRMS,
            product=manifest.product.value,
            request_metadata=metadata
            | {
                "progress": {
                    "completed_days": [],
                    "day": None,
                    "after_key": "",
                    "after_hash": "",
                    "inserted": 0,
                    "revisions": 0,
                    "complete": False,
                }
            },
            retrieved_at=manifest.retrieved_at,
            storage_path=str(prepared.folder / "original.csv"),
            content_hash=manifest.sha256,
            format="csv",
            processing_version=ARCHIVE_VERSION,
        )
        .on_conflict_do_nothing()
    )
    asset = session.scalar(
        select(SourceAsset).where(SourceAsset.id == prepared.import_id).with_for_update()
    )
    assert asset is not None
    if (
        any(asset.request_metadata.get(k) != v for k, v in metadata.items())
        or asset.content_hash != manifest.sha256
        or asset.storage_path != str(prepared.folder / "original.csv")
    ):
        raise ArchiveError("Archive identity conflicts with existing source evidence.")
    return asset


def _day_batch(
    session: Session, prepared: PreparedArchive, day: date, region_id: UUID
) -> IngestionBatch:
    manifest = prepared.manifest
    asset_id = uuid5(prepared.import_id, "asset:" + str(day))
    batch_id = uuid5(prepared.import_id, "batch:" + str(day))
    start = datetime.combine(day, time.min, UTC)
    session.execute(
        insert(SourceAsset)
        .values(
            id=asset_id,
            provider=Provider.FIRMS,
            product=manifest.product.value,
            request_metadata=manifest.request(day).model_dump(mode="json")
            | {"archive_parent_asset_id": str(prepared.import_id)},
            retrieved_at=manifest.retrieved_at,
            storage_path=str(prepared.folder / "original.csv"),
            content_hash=manifest.sha256,
            format="csv",
            processing_version=NORMALIZER_VERSION,
        )
        .on_conflict_do_nothing()
    )
    counts = prepared.quality["daily_counts"][str(day)]
    session.execute(
        insert(IngestionBatch)
        .values(
            id=batch_id,
            region_id=region_id,
            product=manifest.product,
            window_start=start,
            window_end=start + timedelta(days=1),
            status=JobStatus.RUNNING,
            asset_id=asset_id,
            fetched_count=counts.get("fetched", 0),
            rejected_count=counts.get("rejected", 0),
        )
        .on_conflict_do_nothing()
    )
    batch = session.get(IngestionBatch, batch_id)
    assert batch is not None
    return batch


def _write_revision(
    session: Session, batch: IngestionBatch, prepared: PreparedArchive, row: tuple[Any, ...]
) -> bool:
    revision_hash, key, physical, observation, row_numbers, occurrences = row
    dto = DetectionInput.model_validate_json(observation)
    if digest(dto.model_dump(mode="json")) != revision_hash or dto.dedup_key != key:
        raise ArchiveError("Staged archive revision failed its integrity check.")
    detection, created = save_detection_result(session, batch.id, batch.region_id, dto)
    source = session.get(DetectionSource, (detection.id, batch.id))
    assert source is not None
    if source.raw_values.get("schema_version") == "firms-source-revisions-v1":
        history = dict(source.raw_values)
        revisions = list(history["revisions"])
    else:
        history = {
            "schema_version": "firms-source-revisions-v1",
            "normalizer_version": NORMALIZER_VERSION,
            "policy_hash": digest(
                {"version": NORMALIZER_VERSION, "policy": prepared.policy.model_dump()}
            ),
            "archive_parent_asset_id": str(prepared.import_id),
            "revision_occurrences": {},
        }
        revisions = []
    if any(item["revision_hash"] == revision_hash for item in revisions):
        raise ArchiveError("Archive checkpoint and saved revision history disagree.")
    revision = RowRevision(
        revision_hash=revision_hash,
        physical_key=physical,
        row_numbers=json.loads(row_numbers),
        observation=dto,
    )
    revisions.append(revision.model_dump(mode="json"))
    history["revisions"] = revisions
    history["revision_occurrences"] = dict(history["revision_occurrences"]) | {
        revision_hash: occurrences
    }
    source.raw_values = history
    batch.accepted_count += int(created)
    batch.duplicate_count += occurrences - int(created)
    return created


def import_prepared(
    prepared: PreparedArchive, engine: Engine, *, chunk_rows: int = 1000
) -> dict[str, Any]:
    if not 1 <= chunk_rows <= 5000:
        raise ArchiveError("Database chunk size must be between 1 and 5000 revisions.")
    if hash_file(prepared.folder / "original.csv") != prepared.manifest.sha256:
        raise ArchiveError("Preserved archive checksum changed before import.")
    # A session-scoped advisory lock serializes checkpoint owners without holding
    # a transaction open across the import. Individual write transactions stay bounded.
    lock_key = int.from_bytes(prepared.import_id.bytes[:8], "big", signed=True)
    with engine.connect() as lock:
        acquired = lock.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})
        lock.commit()
        if not acquired:
            raise ArchiveError(
                "This archive is already being imported; retry after that process exits."
            )
        try:
            return _import_locked(prepared, lock, chunk_rows)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            lock.commit()


def _import_locked(
    prepared: PreparedArchive, connection: Connection, chunk_rows: int
) -> dict[str, Any]:
    manifest = prepared.manifest
    with Session(connection) as session, session.begin():
        area = session.scalar(select(Region).where(Region.name == manifest.region))
        box = manifest.bbox
        if area is None or (area.west, area.south, area.east, area.north) != (
            box.west,
            box.south,
            box.east,
            box.north,
        ):
            raise ArchiveError("The archive must match an existing configured region and bbox.")
        region_id = area.id
        parent = _registry(session, prepared, region_id)
        initial = dict(parent.request_metadata["progress"])
    with closing(sqlite3.connect(prepared.index_path)) as index:
        for day in manifest.days():
            while True:
                with Session(connection, autoflush=False) as session, session.begin():
                    parent = _registry(session, prepared, region_id)
                    progress = dict(parent.request_metadata["progress"])
                    if str(day) in progress["completed_days"]:
                        break
                    if progress["day"] not in {None, str(day)}:
                        raise ArchiveError("Archive checkpoint calendar order is inconsistent.")
                    batch = _day_batch(session, prepared, day, region_id)
                    if batch.status != JobStatus.RUNNING:
                        raise ArchiveError(
                            "Incomplete archive checkpoint refers to a published day."
                        )
                    after_key = progress["after_key"] if progress["day"] == str(day) else ""
                    after_hash = progress["after_hash"] if progress["day"] == str(day) else ""
                    rows = index.execute(
                        "SELECT hash,key,physical,observation,row_numbers,occurrences "
                        "FROM revisions WHERE day=? AND (key,hash)>(?,?) "
                        "ORDER BY key,hash LIMIT ?",
                        (str(day), after_key, after_hash, chunk_rows),
                    ).fetchall()
                    for row in rows:
                        progress["inserted"] += int(_write_revision(session, batch, prepared, row))
                        progress["revisions"] += 1
                    if rows:
                        progress.update(day=str(day), after_key=rows[-1][1], after_hash=rows[-1][0])
                    else:
                        batch.status = (
                            JobStatus.SUCCEEDED if prepared.selectable(day) else JobStatus.FAILED
                        )
                        batch.failure_reason = (
                            None
                            if prepared.selectable(day)
                            else "Archive date coverage is unverified or unexpectedly empty."
                        )
                        if (
                            batch.accepted_count + batch.duplicate_count + batch.rejected_count
                            != batch.fetched_count
                        ):
                            raise ArchiveError("Archive day counts do not reconcile.")
                        progress["completed_days"] = [*progress["completed_days"], str(day)]
                        progress.update(day=None, after_key="", after_hash="")
                    parent.request_metadata = dict(parent.request_metadata) | {"progress": progress}
                    session.flush()
                atomic_json(prepared.folder / "progress.json", progress)
                if not rows:
                    break
    with Session(connection) as session, session.begin():
        parent = _registry(session, prepared, region_id)
        progress = dict(parent.request_metadata["progress"]) | {"complete": True}
        parent.request_metadata = dict(parent.request_metadata) | {"progress": progress}
    batches = [
        str(uuid5(prepared.import_id, "batch:" + str(day)))
        for day in manifest.days()
        if prepared.selectable(day)
    ]
    report = {
        "schema_version": ARCHIVE_VERSION,
        "import_id": str(prepared.import_id),
        "status": "completed",
        "source_manifest": manifest.model_dump(mode="json"),
        "original_path": str(prepared.folder / "original.csv"),
        "quality_path": str(prepared.folder / "quality.json"),
        "inventory": prepared.quality,
        "source_batch_ids": batches,
        "source_batches": [
            {
                "date": str(day),
                "batch_id": str(uuid5(prepared.import_id, "batch:" + str(day))),
                "coverage_complete": prepared.selectable(day),
            }
            for day in manifest.days()
        ],
        "database": {
            "source_asset_id": str(prepared.import_id),
            "region_id": str(region_id),
            "inserted_observations_total": progress["inserted"],
            "inserted_observations_this_run": progress["inserted"] - initial["inserted"],
            "revision_snapshots_total": progress["revisions"],
            "reused": initial["complete"],
            "chunk_rows": chunk_rows,
        },
        "member_4_handoff": {
            "labels": "not_created",
            "positive_count": None,
            "negative_count": None,
            "weather_and_baseline_history": "separate Phase 3 work required",
            "training_readiness": "not_ready_without_labels_and_independent_event_review",
            "frozen_run_input_policy": "firms-selection-v1",
            "observation_history_complete": prepared.quality["observation_history_complete"],
        },
    }
    atomic_json(prepared.folder / "handoff.json", report)
    atomic_json(prepared.folder / "progress.json", progress)
    return report
