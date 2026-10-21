"""Atomic snapshot stage with immutable source pins and saved diagnostics."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from firewatch.db.models import (
    ClusterMember,
    ClusterSnapshotRecord,
    IngestionBatch,
    Run,
    SourceAsset,
)
from firewatch.processing.clusters import ClusteringSpec, SnapshotBatch, build_snapshots
from firewatch.repositories.revisions import RevisionPolicy, freeze_firms_inputs
from firewatch.schemas.common import JobStatus, Product, RunStatus, Sensor, Stage, StageStatus
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import utc


def required_days(cutoff: datetime) -> list[datetime]:
    cutoff = utc(cutoff)
    first = datetime.combine((cutoff - timedelta(hours=24)).date(), time.min, UTC)
    return [first + timedelta(days=i) for i in range((cutoff.date() - first.date()).days + 1)]


def policy_for_snapshots(
    session: Session,
    region_id: UUID,
    cutoff: datetime,
    products: dict[Sensor, Product],
    retrieval_cutoff: datetime,
    *,
    allow_rejected_batches: bool = False,
) -> RevisionPolicy:
    """Pin latest complete calendar-day sources for the two-day 24h lookup span."""
    cutoff = utc(cutoff)
    available = utc(retrieval_cutoff)
    days = required_days(cutoff)
    query = (
        select(IngestionBatch, SourceAsset)
        .join(SourceAsset, SourceAsset.id == IngestionBatch.asset_id)
        .where(
            IngestionBatch.region_id == region_id,
            IngestionBatch.product.in_(products.values()),
            IngestionBatch.status == JobStatus.SUCCEEDED,
            SourceAsset.retrieved_at <= available,
            IngestionBatch.window_start <= cutoff,
            IngestionBatch.window_end > cutoff - timedelta(hours=24),
        )
        .order_by(SourceAsset.retrieved_at.desc(), IngestionBatch.id.desc())
        .limit(1001)
    )
    sources = session.execute(query).all()
    if len(sources) > 1000:
        raise ValueError("Source selection bound exceeded; pin explicit source batches")
    winners: dict[tuple[Product, datetime], UUID] = {}
    for batch, _asset in sources:
        for day in days:
            if batch.window_start <= day and batch.window_end >= day + timedelta(days=1):
                winners.setdefault((batch.product, day), batch.id)
    if any((product, day) not in winners for product in products.values() for day in days):
        raise ValueError("Missing complete source-day coverage for the 24-hour activity window")
    return RevisionPolicy(
        products=products,
        batch_ids=sorted(set(winners.values()), key=lambda i: i.hex),
        retrieval_cutoff=available,
        allow_rejected_batches=allow_rejected_batches,
    )


def check_coverage(session: Session, run: Run, policy: RevisionPolicy) -> list[str]:
    sources = list(
        session.scalars(select(IngestionBatch).where(IngestionBatch.id.in_(policy.batch_ids)))
    )
    days = required_days(run.as_of)
    if any(
        not any(
            b.product == product
            and b.window_start <= day
            and b.window_end >= day + timedelta(days=1)
            for b in sources
        )
        for product in policy.products.values()
        for day in days
    ):
        raise ValueError("Missing complete source-day coverage for the 24-hour activity window")
    return ["source_rows_rejected"] if any(b.rejected_count for b in sources) else []


def _verify_materialized(session: Session, batch: SnapshotBatch) -> None:
    rows = list(
        session.scalars(
            select(ClusterSnapshotRecord).where(ClusterSnapshotRecord.run_id == batch.run_id)
        )
    )
    expected = {d.snapshot.snapshot_id: d.snapshot for d in batch.snapshots}
    if {r.id for r in rows} != set(expected):
        raise ValueError("Saved snapshot set does not match stage manifest")
    memberships: dict[UUID, set[UUID]] = {}
    for snapshot_id, detection_id in session.execute(
        select(ClusterMember.snapshot_id, ClusterMember.detection_id)
        .join(ClusterSnapshotRecord, ClusterSnapshotRecord.id == ClusterMember.snapshot_id)
        .where(ClusterSnapshotRecord.run_id == batch.run_id)
    ):
        memberships.setdefault(snapshot_id, set()).add(detection_id)
    for row in rows:
        item = expected[row.id]
        for name in (
            "latitude",
            "longitude",
            "frp_max_mw",
            "frp_sum_mw",
            "latest_detection_at",
            "algorithm_version",
        ):
            if getattr(row, name) != getattr(item, name):
                raise ValueError("Saved snapshot values do not match stage manifest")
        if memberships.get(row.id, set()) != set(item.member_ids) or row.member_count != len(
            item.member_ids
        ):
            raise ValueError("Saved snapshot membership does not match stage manifest")


def build_run_snapshots(
    session: Session,
    run_id: UUID,
    policy: RevisionPolicy,
    config: ClusteringSpec | None = None,
) -> SnapshotBatch:
    """Caller owns transaction; lock one run, pin inputs and publish this stage atomically.

    This does not complete a pipeline Run or advance the latest-live pointer.
    Empty successful batches are persisted as an explicit stage manifest too.
    """
    if not session.in_transaction():
        raise ValueError("Use a caller-owned snapshot transaction")
    config = ClusteringSpec.model_validate((config or ClusteringSpec()).model_dump())
    policy = RevisionPolicy.model_validate(policy.model_dump(mode="json"))
    run = session.scalar(select(Run).where(Run.id == run_id).with_for_update())
    if run is None:
        raise ValueError("Unknown snapshot run")
    existing = run.input_asset_manifest.get("clustering")
    if existing is None and run.status != RunStatus.RUNNING:
        raise ValueError("Only running runs can build new snapshots")
    if existing is not None:
        saved = SnapshotBatch.model_validate(existing["batch"])
        if saved.config != config or digest(existing["batch"]) != existing["sha256"]:
            raise ValueError("Snapshot configuration or manifest changed")
    quality = check_coverage(session, run, policy)
    inputs = freeze_firms_inputs(session, run_id, run.as_of - timedelta(hours=24), policy)
    computed = build_snapshots(
        run.id, run.mode, run.as_of, inputs, config, feature_version=run.feature_version
    )
    computed.quality_flags = quality
    for details in computed.snapshots:
        details.quality_flags = sorted(set(details.quality_flags + quality))
    if existing is not None:
        if saved != computed:
            raise ValueError(
                "Saved snapshots cannot change their pinned inputs or algorithm environment"
            )
        _verify_materialized(session, saved)
        return saved
    if (
        session.scalar(
            select(ClusterSnapshotRecord.id).where(ClusterSnapshotRecord.run_id == run_id).limit(1)
        )
        is not None
    ):
        raise ValueError("Run has snapshots without a snapshot stage manifest")
    for details in computed.snapshots:
        snapshot = details.snapshot
        session.add(
            ClusterSnapshotRecord(
                id=snapshot.snapshot_id,
                run_id=run.id,
                latitude=snapshot.latitude,
                longitude=snapshot.longitude,
                member_count=len(snapshot.member_ids),
                frp_max_mw=snapshot.frp_max_mw,
                frp_sum_mw=snapshot.frp_sum_mw,
                latest_detection_at=snapshot.latest_detection_at,
                algorithm_version=snapshot.algorithm_version,
            )
        )
    session.flush()
    for details in computed.snapshots:
        snapshot = details.snapshot
        session.add_all(
            [
                ClusterMember(snapshot_id=snapshot.snapshot_id, detection_id=identifier)
                for identifier in snapshot.member_ids
            ]
        )
    payload = computed.model_dump(mode="json")
    run.input_asset_manifest = dict(run.input_asset_manifest) | {
        "clustering": {"batch": payload, "sha256": digest(payload)}
    }
    run.stage_status = dict(run.stage_status) | {Stage.SNAPSHOTS.value: StageStatus.SUCCEEDED.value}
    session.flush()
    _verify_materialized(session, computed)
    return computed
