"""Durable FIRMS refresh progress and per-attempt ingestion telemetry."""

from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from firewatch.db.models import IngestionBatch, SourceAsset
from firewatch.repositories.ingestion import IngestionResult, persist_normalized
from firewatch.schemas.common import JobStatus, Product, Provider
from firewatch.schemas.firms_refresh import REFRESH_VERSION, RefreshChunk, RefreshState
from firewatch.sources.firms_normalize import NORMALIZER_VERSION, NormalizedFirmsBatch, digest


def load_refresh(
    session: Session, identifier: UUID, *, locked: bool = False
) -> tuple[SourceAsset, RefreshState]:
    query = select(SourceAsset).where(
        SourceAsset.id == identifier, SourceAsset.product == REFRESH_VERSION
    )
    if locked:
        query = query.with_for_update()
    asset = session.scalar(query)
    if asset is None:
        raise ValueError("Unknown FIRMS refresh")
    state = RefreshState.model_validate(asset.request_metadata["state"])
    if state.refresh_id != identifier:
        raise ValueError("Refresh identity mismatch")
    return asset, state


def save_refresh(session: Session, state: RefreshState, folder: Path) -> None:
    metadata = {"schema_version": REFRESH_VERSION, "state": state.model_dump(mode="json")}
    session.execute(
        insert(SourceAsset)
        .values(
            id=state.refresh_id,
            provider=Provider.FIRMS,
            product=REFRESH_VERSION,
            request_metadata=metadata,
            retrieved_at=state.updated_at,
            storage_path=str(folder / "request.json"),
            content_hash=digest(state.spec.model_dump(mode="json")),
            format="json",
            processing_version=REFRESH_VERSION,
        )
        .on_conflict_do_nothing()
    )
    asset = session.get(SourceAsset, state.refresh_id)
    assert asset is not None
    prior = RefreshState.model_validate(asset.request_metadata["state"])
    if (
        prior.spec != state.spec
        or prior.region_id != state.region_id
        or prior.bbox != state.bbox
        or asset.storage_path != str(folder / "request.json")
    ):
        raise ValueError("Refresh key conflicts with another request or output directory")
    asset.request_metadata = metadata
    asset.retrieved_at = state.updated_at
    session.flush()


def reserve_attempt(session: Session, state: RefreshState, chunk: RefreshChunk, path: Path) -> None:
    assert chunk.fetch_id is not None and chunk.attempted_at is not None
    policy_hash = digest(
        {"version": NORMALIZER_VERSION, "policy": state.spec.normalization_policy.model_dump()}
    )
    chunk.batch_id = uuid5(chunk.fetch_id, f"{policy_hash}:{state.region_id}")
    asset_id = uuid5(chunk.fetch_id, "attempt")
    session.add(
        SourceAsset(
            id=asset_id,
            provider=Provider.FIRMS,
            product=chunk.request.product.value,
            request_metadata={
                "refresh_id": str(state.refresh_id),
                "request": chunk.request.model_dump(mode="json"),
                "counts_known": False,
            },
            retrieved_at=chunk.attempted_at,
            storage_path=str(path),
            content_hash=digest(chunk.request.model_dump(mode="json")),
            format="json",
            processing_version="firms-refresh-attempt-v1",
        )
    )
    session.flush()
    start = datetime.combine(chunk.request.start_date, time.min, UTC)
    session.add(
        IngestionBatch(
            id=chunk.batch_id,
            region_id=state.region_id,
            product=chunk.request.product,
            window_start=start,
            window_end=start + timedelta(days=chunk.request.days),
            status=JobStatus.RUNNING,
            asset_id=asset_id,
        )
    )
    session.flush()


def fail_attempt(session: Session, chunk: RefreshChunk, finished: datetime, code: str) -> None:
    assert chunk.batch_id is not None
    batch = session.get(IngestionBatch, chunk.batch_id)
    if batch is None or batch.status != JobStatus.RUNNING:
        raise ValueError("Refresh attempt cannot transition from its saved state")
    batch.status = JobStatus.FAILED
    batch.failure_reason = code
    asset = session.get(SourceAsset, batch.asset_id)
    assert asset is not None
    # Failed attempts have a timestamp, never a successful retrieval or known zero count.
    asset.retrieved_at = finished
    asset.request_metadata = dict(asset.request_metadata) | {
        "error_code": code,
        "counts_known": False,
    }


def complete_attempt(
    session: Session, state: RefreshState, chunk: RefreshChunk, normalized: NormalizedFirmsBatch
) -> IngestionResult:
    batch = normalized.detection_batch(state.region_id)
    if (
        batch.batch_id != chunk.batch_id
        or normalized.source_batch_id != chunk.fetch_id
        or normalized.request != chunk.request
    ):
        raise ValueError("Fetched source does not belong to the reserved refresh attempt")
    # The reservation has no detection children. Replace it atomically with the
    # normalizer's canonical batch ID/asset, retaining all existing repository guards.
    removed = session.execute(
        delete(IngestionBatch)
        .where(IngestionBatch.id == chunk.batch_id, IngestionBatch.status == JobStatus.RUNNING)
        .returning(IngestionBatch.id)
    ).scalar_one_or_none()
    if removed is None:
        raise ValueError("Refresh attempt was already changed")
    return persist_normalized(session, normalized, state.region_id)


def latest_refreshes(session: Session, clock: datetime) -> dict[tuple[UUID, Product], RefreshState]:
    region = SourceAsset.request_metadata["state"]["region_id"].astext
    requested = SourceAsset.request_metadata["state"]["spec"]["products"]
    result = {}
    for product in (Product.NOAA20_NRT, Product.NOAA21_NRT):
        assets = session.scalars(
            select(SourceAsset)
            .where(
                SourceAsset.product == REFRESH_VERSION,
                SourceAsset.provider == Provider.FIRMS,
                SourceAsset.retrieved_at <= clock,
                requested.contains([product.value]),
            )
            .distinct(region)
            .order_by(region, SourceAsset.retrieved_at.desc(), SourceAsset.id.desc())
        ).all()
        for asset in assets:
            state = RefreshState.model_validate(asset.request_metadata["state"])
            result[(state.region_id, product)] = state
    return result
