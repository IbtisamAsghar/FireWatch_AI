"""Observation uniqueness with many-region and many-source lineage."""

from uuid import UUID

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firewatch.db.models import Detection, DetectionRegion, DetectionSource, IngestionBatch, Region
from firewatch.schemas.contracts import DetectionInput


def save_detection(
    session: Session, batch_id: UUID, region_id: UUID, observation: DetectionInput
) -> Detection:
    return save_detection_result(session, batch_id, region_id, observation)[0]


def save_detection_result(
    session: Session, batch_id: UUID, region_id: UUID, observation: DetectionInput
) -> tuple[Detection, bool]:
    """Return the DB-resolved identity and whether this transaction inserted it."""
    batch = session.get(IngestionBatch, batch_id)
    region = session.get(Region, region_id)
    if batch is None or region is None or batch.region_id != region_id:
        raise ValueError("Detection batch must belong to the specified region")
    if batch.product != observation.product or not (
        batch.window_start <= observation.acquired_at <= batch.window_end
    ):
        raise ValueError("Detection must match batch product and UTC window")
    if not (
        region.west <= observation.longitude <= region.east
        and region.south <= observation.latitude <= region.north
    ):
        raise ValueError("Detection is outside the requested region")
    statement = (
        insert(Detection)
        .values(primary_batch_id=batch_id, **observation.model_dump())
        .on_conflict_do_nothing(index_elements=[Detection.dedup_key])
        .returning(Detection.id)
    )
    identifier = session.execute(statement).scalar_one_or_none()
    if identifier is None:
        from sqlalchemy import select

        detection = session.scalars(
            select(Detection).where(Detection.dedup_key == observation.dedup_key)
        ).one()
    else:
        inserted = session.get(Detection, identifier)
        assert inserted is not None
        detection = inserted
    identity = ("product", "sensor", "latitude", "longitude", "acquired_at", "scan", "track")
    if any(getattr(detection, field) != getattr(observation, field) for field in identity):
        raise ValueError("Deduplication key collides with another observation identity")
    session.execute(
        insert(DetectionSource)
        .values(
            detection_id=detection.id,
            batch_id=batch_id,
            raw_values=observation.model_dump(mode="json"),
        )
        .on_conflict_do_nothing()
    )
    session.execute(
        insert(DetectionRegion)
        .values(detection_id=detection.id, region_id=region_id, acquired_at=detection.acquired_at)
        .on_conflict_do_nothing()
    )
    return detection, identifier is not None
