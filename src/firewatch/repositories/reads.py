"""SQL projections shared across list/detail/GeoJSON/summary and status routes."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Row, Select, func, select
from sqlalchemy.orm import Session

from firewatch.db.models import (
    Alert,
    ClusterSnapshotRecord,
    FeatureSetRecord,
    IngestionBatch,
    LiveRunHead,
    Region,
    Run,
    Score,
    SourceAsset,
)
from firewatch.schemas.api import HotspotFilters, RunQuery, parse_bbox
from firewatch.schemas.common import JobStatus, RunStatus


def require_run(session: Session, identifier: UUID, completed: bool = True) -> Run:
    from firewatch.api.errors import APIError

    run = session.get(Run, identifier)
    if run is None:
        raise APIError(404, "RUN_NOT_FOUND", "The requested run was not found.")
    if completed and run.status != RunStatus.COMPLETED:
        raise APIError(503, "RUN_NOT_READY", "The requested run is not completed.", True)
    return run


def hotspot_selection(run: Run, filters: HotspotFilters) -> Select[Any]:
    statement = (
        select(ClusterSnapshotRecord, Score, FeatureSetRecord, Alert.id.label("alert_id"))
        .join(
            Score,
            (Score.snapshot_id == ClusterSnapshotRecord.id)
            & (Score.run_id == ClusterSnapshotRecord.run_id)
            & (Score.scorer_version == run.scorer_version),
        )
        .join(
            FeatureSetRecord,
            (FeatureSetRecord.snapshot_id == ClusterSnapshotRecord.id)
            & (FeatureSetRecord.run_id == run.id),
        )
        .outerjoin(Alert, Alert.score_id == Score.id)
        .where(ClusterSnapshotRecord.run_id == run.id)
    )
    if filters.priority is not None:
        statement = statement.where(Score.priority == filters.priority)
    if filters.bbox is not None:
        box = parse_bbox(filters.bbox)
        statement = statement.where(
            ClusterSnapshotRecord.longitude.between(box.west, box.east),
            ClusterSnapshotRecord.latitude.between(box.south, box.north),
        )
    return statement


def ranked_page(
    session: Session, statement: Select[Any], limit: int, offset: int
) -> tuple[int, Sequence[Row[Any]]]:
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = session.execute(
        statement.order_by(Score.priority_score.desc(), ClusterSnapshotRecord.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return total, rows


def run_selection(query: RunQuery) -> Select[tuple[Run]]:
    statement = select(Run)
    if query.region_id is not None:
        statement = statement.where(Run.region_id == query.region_id)
    if query.mode is not None:
        statement = statement.where(Run.mode == query.mode)
    if query.status is not None:
        statement = statement.where(Run.status == query.status)
    else:
        statement = statement.where(Run.status.in_([RunStatus.COMPLETED, RunStatus.FAILED]))
    return statement


def run_times(session: Session, run: Run) -> tuple[datetime | None, datetime | None]:
    observed = session.scalar(
        select(func.max(ClusterSnapshotRecord.latest_detection_at)).where(
            ClusterSnapshotRecord.run_id == run.id
        )
    )
    scored = session.scalar(
        select(func.max(Score.scored_at)).where(
            Score.run_id == run.id, Score.scorer_version == run.scorer_version
        )
    )
    return observed, scored


def region_rows(session: Session) -> Sequence[Row[Any]]:
    return session.execute(
        select(Region, LiveRunHead.run_id)
        .outerjoin(LiveRunHead, Region.id == LiveRunHead.region_id)
        .order_by(Region.name)
    ).all()


def source_attempts(session: Session) -> tuple[Sequence[Row[Any]], Sequence[Row[Any]]]:
    latest = session.execute(
        select(IngestionBatch, SourceAsset.retrieved_at)
        .join(SourceAsset, SourceAsset.id == IngestionBatch.asset_id)
        .distinct(IngestionBatch.region_id, IngestionBatch.product)
        .order_by(
            IngestionBatch.region_id,
            IngestionBatch.product,
            SourceAsset.retrieved_at.desc(),
            IngestionBatch.id.desc(),
        )
    ).all()
    successes = session.execute(
        select(IngestionBatch.region_id, IngestionBatch.product, func.max(SourceAsset.retrieved_at))
        .join(SourceAsset, SourceAsset.id == IngestionBatch.asset_id)
        .where(IngestionBatch.status == JobStatus.SUCCEEDED)
        .group_by(IngestionBatch.region_id, IngestionBatch.product)
    ).all()
    return latest, successes
