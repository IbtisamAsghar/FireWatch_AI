"""Explicit product/dataset selection and immutable run pins for source corrections."""

from datetime import UTC, datetime, time, timedelta
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from firewatch.db.models import Detection, DetectionSource, IngestionBatch, Run, SourceAsset
from firewatch.schemas.common import DTO, Digest, JobStatus, Product, RunStatus, Sensor, UTCDateTime
from firewatch.schemas.contracts import DetectionInput
from firewatch.sources.firms_normalize import RowRevision, digest


class RevisionPolicy(DTO):
    schema_version: Literal["firms-selection-v1"] = "firms-selection-v1"
    products: dict[Sensor, Product]
    batch_ids: list[UUID] = Field(min_length=1, max_length=128)
    retrieval_cutoff: UTCDateTime
    allow_rejected_batches: bool = False
    max_observations: int = Field(default=100000, ge=1, le=500000)

    @model_validator(mode="after")
    def products_and_batches(self) -> Self:
        if not self.products or len(set(self.batch_ids)) != len(self.batch_ids):
            raise ValueError("Pin unique batch IDs and at least one sensor/product")
        for sensor, product in self.products.items():
            expected = Sensor.NOAA20 if "NOAA20" in product else Sensor.NOAA21
            if sensor != expected:
                raise ValueError("Selected product does not match its sensor")
        return self


class SelectedDetection(DTO):
    detection_id: UUID
    batch_id: UUID
    revision_hash: Digest
    physical_key: Digest
    observation: DetectionInput


def source_revisions(source: DetectionSource) -> list[RowRevision]:
    data = source.raw_values
    if data.get("schema_version") != "firms-source-revisions-v1":
        raise ValueError("Selected source has no versioned normalized revision history")
    rows = [RowRevision.model_validate(item) for item in data["revisions"]]
    for revision in rows:
        metadata = revision.observation.raw_values.get("normalization")
        if (
            not isinstance(metadata, dict)
            or metadata.get("physical_key") != revision.physical_key
            or digest(revision.observation.model_dump(mode="json")) != revision.revision_hash
        ):
            raise ValueError("Saved source revision failed its integrity check")
    return rows


def select_revisions(
    session: Session,
    region_id: UUID,
    window_start: datetime,
    window_end: datetime,
    policy: RevisionPolicy,
) -> list[SelectedDetection]:
    policy = RevisionPolicy.model_validate(policy.model_dump(mode="json"))
    if (
        any(t.tzinfo is None or t.utcoffset() is None for t in (window_start, window_end))
        or window_start >= window_end
    ):
        raise ValueError("Selection requires an ordered aware UTC window")
    sources = session.execute(
        select(IngestionBatch, SourceAsset)
        .join(SourceAsset, SourceAsset.id == IngestionBatch.asset_id)
        .where(IngestionBatch.id.in_(policy.batch_ids))
    ).all()
    if len(sources) != len(policy.batch_ids):
        raise ValueError("A pinned source batch is unavailable")
    winners: dict[object, UUID] = {}
    ranks: dict[UUID, tuple[datetime, str]] = {}
    for batch, asset in sources:
        if (
            batch.region_id != region_id
            or batch.product not in policy.products.values()
            or batch.status != JobStatus.SUCCEEDED
            or asset.retrieved_at > policy.retrieval_cutoff
            or (batch.rejected_count and not policy.allow_rejected_batches)
        ):
            raise ValueError(
                "A pinned batch violates region, product, quality, status or availability policy"
            )
        start, end = batch.window_start.astimezone(UTC), batch.window_end.astimezone(UTC)
        if start.time() != time.min or end.time() != time.min or not 1 <= (end - start).days <= 5:
            raise ValueError("Selection expects complete calendar-day fetch windows")
        ranks[batch.id] = (asset.retrieved_at, batch.id.hex)
        for offset in range((end - start).days):
            # A later full-region response replaces the entire overlapping day,
            # including moved/removed detections and valid empty responses.
            key = (batch.product, start.date() + timedelta(days=offset))
            previous = winners.get(key)
            if previous is None or ranks[batch.id] > ranks[previous]:
                winners[key] = batch.id
    records = session.execute(
        select(DetectionSource, Detection)
        .join(Detection, Detection.id == DetectionSource.detection_id)
        .where(
            DetectionSource.batch_id.in_(set(winners.values())),
            Detection.acquired_at >= window_start,
            Detection.acquired_at < window_end,
        )
        .limit(policy.max_observations + 1)
    ).all()
    if len(records) > policy.max_observations:
        raise ValueError("Selected source dataset exceeds the observation bound")
    selected: dict[UUID, SelectedDetection] = {}
    for source, detection in records:
        if (
            winners[(detection.product, detection.acquired_at.astimezone(UTC).date())]
            != source.batch_id
        ):
            continue
        revisions = source_revisions(source)
        if not revisions:
            raise ValueError("A selected observation has no revisions")
        revision = max(revisions, key=lambda item: item.revision_hash)
        if (
            revision.observation.dedup_key != detection.dedup_key
            or revision.observation.product != detection.product
        ):
            raise ValueError("Revision does not belong to its saved observation")
        selected[detection.id] = SelectedDetection(
            detection_id=detection.id,
            batch_id=source.batch_id,
            revision_hash=revision.revision_hash,
            physical_key=revision.physical_key,
            observation=revision.observation,
        )
    return sorted(
        selected.values(), key=lambda item: (item.observation.acquired_at, item.detection_id.hex)
    )


def freeze_firms_inputs(
    session: Session, run_id: UUID, window_start: datetime, policy: RevisionPolicy
) -> list[SelectedDetection]:
    """Call inside a write transaction before pipeline outputs; never repin a run."""
    policy = RevisionPolicy.model_validate(policy.model_dump(mode="json"))
    if not session.in_transaction():
        raise ValueError("Use a caller-owned transaction")
    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise ValueError("Unknown run")
    if window_start.tzinfo is None or window_start.utcoffset() is None or window_start > run.as_of:
        raise ValueError("Run input window must be aware and must precede its cutoff")
    expected = {
        "policy": policy.model_dump(mode="json"),
        "window_start": window_start.astimezone(UTC).isoformat(),
    }
    existing = run.input_asset_manifest.get("firms")
    if existing is not None:
        if any(existing.get(key) != value for key, value in expected.items()):
            raise ValueError("A run cannot be repinned to different FIRMS inputs")
        result = []
        for saved in existing["selected"]:
            source = session.get(
                DetectionSource, (UUID(saved["detection_id"]), UUID(saved["batch_id"]))
            )
            if source is None:
                raise ValueError("Pinned observation lineage is unavailable")
            matches = [
                row
                for row in source_revisions(source)
                if row.revision_hash == saved["revision_hash"]
            ]
            if len(matches) != 1:
                raise ValueError("Pinned source revision is unavailable")
            revision = matches[0]
            result.append(
                SelectedDetection(
                    detection_id=saved["detection_id"],
                    batch_id=saved["batch_id"],
                    revision_hash=revision.revision_hash,
                    physical_key=revision.physical_key,
                    observation=revision.observation,
                )
            )
        return result
    if run.status != RunStatus.RUNNING:
        raise ValueError("Only a running run can initially pin source inputs")
    selected = select_revisions(
        session, run.region_id, window_start, run.as_of + timedelta(microseconds=1), policy
    )
    run.input_asset_manifest = dict(run.input_asset_manifest) | {
        "firms": expected
        | {
            "selected": [
                {
                    "detection_id": str(item.detection_id),
                    "batch_id": str(item.batch_id),
                    "revision_hash": item.revision_hash,
                }
                for item in selected
            ]
        }
    }
    session.flush()
    return selected
