"""Read-only selection of pinned, complete historical follow-up source days."""

from datetime import datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from firewatch.db.models import IngestionBatch, Region, SourceAsset
from firewatch.ml.labels import (
    PRIMARY,
    DaySource,
    FollowupEvidence,
    Outage,
    followup_days,
)
from firewatch.repositories.revisions import RevisionPolicy, select_revisions
from firewatch.schemas.common import Product
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.open_meteo import utc


def select_followup(
    session: Session,
    region_id: UUID,
    as_of: datetime,
    retrieval_cutoff: datetime,
    outages: list[Outage] | None = None,
) -> FollowupEvidence:
    cutoff, available = utc(as_of), utc(retrieval_cutoff)
    area = session.get(Region, region_id)
    if area is None:
        raise ValueError("Unknown label region")
    end = cutoff + timedelta(hours=24)
    candidates = session.execute(
        select(IngestionBatch, SourceAsset)
        .join(SourceAsset, SourceAsset.id == IngestionBatch.asset_id)
        .where(
            IngestionBatch.region_id == region_id,
            IngestionBatch.product == Product.NOAA20_SP,
            SourceAsset.retrieved_at <= available,
            IngestionBatch.window_start <= end,
            IngestionBatch.window_end > cutoff,
        )
        .order_by(SourceAsset.retrieved_at.desc(), IngestionBatch.id.desc())
        .limit(1001)
    ).all()
    if len(candidates) > 1000:
        raise ValueError("Follow-up source selection exceeds bound")
    days = []
    for day in followup_days(cutoff):
        start = datetime.combine(day, time.min, cutoff.tzinfo)
        match = next(
            (
                (batch, asset)
                for batch, asset in candidates
                if batch.window_start < start + timedelta(days=1) and batch.window_end > start
            ),
            None,
        )
        if match is None:
            continue
        batch, asset = match
        days.append(
            DaySource(
                day=day,
                product=batch.product,
                batch_id=batch.id,
                asset_id=asset.id,
                source_sha256=asset.content_hash,
                retrieved_at=asset.retrieved_at,
                window_start=batch.window_start,
                window_end=batch.window_end,
                status=batch.status,
                rejected_count=batch.rejected_count,
            )
        )
    ids = sorted({day.batch_id for day in days if day.complete}, key=str)
    policy = (
        RevisionPolicy(products=PRIMARY, batch_ids=ids, retrieval_cutoff=available) if ids else None
    )
    rows = (
        select_revisions(session, region_id, cutoff, end + timedelta(microseconds=1), policy)
        if policy
        else []
    )
    return FollowupEvidence(
        region_id=region_id,
        bbox=BoundingBox(west=area.west, south=area.south, east=area.east, north=area.north),
        as_of=cutoff,
        end=end,
        retrieval_cutoff=available,
        source_days=days,
        policy=policy,
        observations=rows,
        outages=outages or [],
    )
