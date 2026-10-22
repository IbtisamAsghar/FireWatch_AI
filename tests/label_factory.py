"""Synthetic Phase 4.1 inputs; never used as real-data evidence."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from cluster_factory import T, selected
from feature_factory import inputs, observation

from firewatch.ml.labels import PRIMARY, DaySource, FollowupEvidence, TargetInput
from firewatch.processing.features import build_features
from firewatch.repositories.revisions import RevisionPolicy
from firewatch.schemas.common import JobStatus, Product
from firewatch.schemas.contracts import BoundingBox

REGION = uuid5(NAMESPACE_URL, "synthetic-label-region")


def current(frp=10.0, cutoff=T, rows=None):
    args = inputs(rows or [observation(1, frp=frp, acquired=cutoff)], cutoff=cutoff)
    return TargetInput(
        region_id=REGION,
        region_name="northern-california",
        products=PRIMARY,
        detail=args[0].snapshots[0],
        feature=build_features(*args)[0],
    )


def followup(rows=None, cutoff=T, bbox=None):
    rows = rows or []
    batch = selected(2).batch_id
    days = [cutoff.date(), (cutoff + timedelta(hours=24)).date()]
    retrieved = cutoff + timedelta(days=3)
    from datetime import UTC, datetime, time

    sources = [
        DaySource(
            day=day,
            product=Product.NOAA20_SP,
            batch_id=batch,
            asset_id=uuid5(NAMESPACE_URL, "synthetic-label-asset"),
            source_sha256="a" * 64,
            retrieved_at=retrieved,
            window_start=datetime.combine(day, time.min, UTC),
            window_end=datetime.combine(day, time.min, UTC) + timedelta(days=1),
            status=JobStatus.SUCCEEDED,
            rejected_count=0,
        )
        for day in days
    ]
    return FollowupEvidence(
        region_id=REGION,
        bbox=bbox or BoundingBox(west=-1, south=-1, east=1, north=1),
        as_of=cutoff,
        end=cutoff + timedelta(hours=24),
        retrieval_cutoff=retrieved,
        source_days=sources,
        policy=RevisionPolicy(products=PRIMARY, batch_ids=[batch], retrieval_cutoff=retrieved),
        observations=rows,
    )


def later(i=10, hours=1, **kw):
    return selected(i, acquired=T + timedelta(hours=hours), **kw)
