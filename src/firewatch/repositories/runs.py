"""Serialize publication by region; completion and latest pointer commit together."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from firewatch.db.models import LiveRunHead, Region, Run
from firewatch.schemas.common import Completeness, RunMode, RunStatus, Stage, StageStatus


def complete_and_publish_run(
    session: Session, run_id: UUID, completed_at: datetime | None = None
) -> Run:
    """Caller must use Database.transaction(); database guards also protect raw SQL."""
    # Obtain publication locks before flushing pending output/stage writes. The
    # worker's Database sessions disable autoflush; refreshing before flush would
    # silently discard the caller's pending final stage checkpoint.
    with session.no_autoflush:
        run = session.get(Run, run_id)
        if run is None:
            raise ValueError("Unknown run")
        session.execute(select(Region.id).where(Region.id == run.region_id).with_for_update()).one()
        session.execute(select(Run.id).where(Run.id == run_id).with_for_update()).one()
    session.flush()
    session.refresh(run)
    if run.status != RunStatus.COMPLETED:
        if run.status != RunStatus.RUNNING:
            raise ValueError("Only a running run can complete")
        if any(run.stage_status.get(stage.value) != StageStatus.SUCCEEDED for stage in Stage):
            raise ValueError("All required pipeline stages must succeed")
        if run.completeness == Completeness.UNAVAILABLE:
            raise ValueError("Unavailable input coverage cannot be published")
        timestamp = completed_at if completed_at is not None else datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("Completion time must be timezone-aware")
        run.completed_at = timestamp.astimezone(UTC)
        run.status = RunStatus.COMPLETED
        session.flush()  # SQL guards verify memberships/features/scores/evidence first.
    if run.mode == RunMode.LIVE:
        head = session.get(LiveRunHead, run.region_id)
        if head is None:
            session.add(LiveRunHead(region_id=run.region_id, run_id=run.id))
        elif head.run_id != run.id:
            previous = session.get(Run, head.run_id)
            assert previous is not None
            if run.as_of > previous.as_of:
                head.run_id = run.id
        session.flush()
    return run
