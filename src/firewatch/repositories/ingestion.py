"""Atomic, idempotent normalized FIRMS persistence using existing uniqueness guards."""

import hashlib
from collections import defaultdict
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firewatch.db.models import DetectionSource, IngestionBatch, Region, SourceAsset
from firewatch.repositories.detections import save_detection_result
from firewatch.schemas.common import DTO, JobStatus
from firewatch.sources.firms_normalize import (
    NORMALIZER_VERSION,
    NormalizedFirmsBatch,
    RowRevision,
    digest,
    write_quality_reports,
)


class IngestionResult(DTO):
    asset_id: UUID
    batch_id: UUID
    fetched_count: int
    inserted_count: int
    duplicate_count: int
    rejected_count: int
    source_revision_count: int
    reused: bool


def persist_normalized(
    session: Session, normalized: NormalizedFirmsBatch, region_id: UUID
) -> IngestionResult:
    """Caller owns the transaction; a failure rolls back assets, rows and lineage."""
    if not session.in_transaction():
        raise ValueError("Use a caller-owned write transaction")
    area = session.get(Region, region_id)
    box = normalized.request.bbox
    if area is None or (area.west, area.south, area.east, area.north) != (
        box.west,
        box.south,
        box.east,
        box.north,
    ):
        raise ValueError("A normalized source batch must cover the configured region bbox exactly")
    with Path(normalized.asset.storage_path).open("rb") as stream:
        raw = stream.read(32 * 1024 * 1024 + 1)
    if (
        len(raw) > 32 * 1024 * 1024
        or hashlib.sha256(raw).hexdigest() != normalized.asset.content_hash
    ):
        raise ValueError("Raw evidence changed before ingestion")
    if normalized.report.source_sha256 != normalized.asset.content_hash:
        raise ValueError("Quality report does not match its source")
    for revision in normalized.revisions:
        if digest(revision.observation.model_dump(mode="json")) != revision.revision_hash:
            raise ValueError("Normalized revision digest does not match")
    write_quality_reports(normalized, Path(normalized.asset.storage_path).parent)
    batch = normalized.detection_batch(region_id)
    session.execute(
        insert(SourceAsset)
        .values(id=batch.asset_id, **normalized.asset.model_dump())
        .on_conflict_do_nothing(index_elements=[SourceAsset.id])
    )
    asset = session.get(SourceAsset, batch.asset_id)
    assert asset is not None
    if any(getattr(asset, key) != value for key, value in normalized.asset.model_dump().items()):
        raise ValueError("Source asset UUID collides with different evidence")
    created = session.execute(
        insert(IngestionBatch)
        .values(
            id=batch.batch_id,
            region_id=region_id,
            product=batch.product,
            window_start=batch.window_start,
            window_end=batch.window_end,
            status=JobStatus.RUNNING,
            asset_id=batch.asset_id,
        )
        .on_conflict_do_nothing(index_elements=[IngestionBatch.id])
        .returning(IngestionBatch.id)
    ).scalar_one_or_none()
    stored = session.scalar(
        select(IngestionBatch).where(IngestionBatch.id == batch.batch_id).with_for_update()
    )
    assert stored is not None
    if (
        stored.region_id,
        stored.product,
        stored.asset_id,
        stored.window_start,
        stored.window_end,
    ) != (batch.region_id, batch.product, batch.asset_id, batch.window_start, batch.window_end):
        raise ValueError("Ingestion batch UUID collides with another input")
    if created is None:
        if (
            stored.status != JobStatus.SUCCEEDED
            or stored.fetched_count != normalized.report.fetched_count
        ):
            raise ValueError("Existing normalized batch is incomplete or inconsistent")
        return IngestionResult(
            asset_id=batch.asset_id,
            batch_id=batch.batch_id,
            fetched_count=stored.fetched_count,
            inserted_count=0,
            duplicate_count=stored.fetched_count - stored.rejected_count,
            rejected_count=stored.rejected_count,
            source_revision_count=0,
            reused=True,
        )
    groups: dict[str, list[RowRevision]] = defaultdict(list)
    for revision in normalized.revisions:
        groups[revision.observation.dedup_key].append(revision)
    inserted_count = 0
    for key in sorted(groups):
        revisions = groups[key]
        detection, inserted = save_detection_result(
            session, batch.batch_id, region_id, revisions[0].observation
        )
        inserted_count += inserted
        source = session.scalar(
            select(DetectionSource)
            .where(
                DetectionSource.detection_id == detection.id,
                DetectionSource.batch_id == batch.batch_id,
            )
            .with_for_update()
        )
        assert source is not None
        if source.raw_values != revisions[0].observation.model_dump(mode="json"):
            raise ValueError("An existing source association conflicts with its normalized batch")
        source.raw_values = {
            "schema_version": "firms-source-revisions-v1",
            "normalizer_version": NORMALIZER_VERSION,
            "policy_hash": normalized.report.policy_hash,
            "revisions": [item.model_dump(mode="json") for item in revisions],
        }
    duplicate_count = (
        normalized.report.fetched_count - normalized.report.rejected_count - inserted_count
    )
    stored.fetched_count = normalized.report.fetched_count
    stored.accepted_count = inserted_count
    stored.rejected_count = normalized.report.rejected_count
    stored.duplicate_count = duplicate_count
    stored.status = JobStatus.SUCCEEDED
    session.flush()
    return IngestionResult(
        asset_id=batch.asset_id,
        batch_id=batch.batch_id,
        fetched_count=stored.fetched_count,
        inserted_count=inserted_count,
        duplicate_count=duplicate_count,
        rejected_count=stored.rejected_count,
        source_revision_count=len(normalized.revisions),
        reused=False,
    )
