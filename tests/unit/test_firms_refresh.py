from datetime import timedelta
from uuid import uuid4

import pytest
from refresh_factory import NOW

from firewatch.schemas.common import Product
from firewatch.schemas.contracts import BoundingBox
from firewatch.schemas.firms_refresh import (
    RefreshChunk,
    RefreshCounts,
    RefreshSpec,
    RefreshState,
    summarize_refresh,
)

BOX = BoundingBox(west=70, south=33, east=74, north=36)


def test_exact_overlap_and_provider_calendar_rounding():
    spec = RefreshSpec(region="test", as_of=NOW)
    assert spec.as_of - spec.window_start == timedelta(hours=48)
    requests = spec.requests(BOX)
    assert len(requests) == 2
    assert all(r.days == 3 and r.start_date == spec.window_start.date() for r in requests)
    one = RefreshSpec(region="test", as_of=NOW, chunk_days=1).requests(BOX)
    assert len(one) == 6 and all(r.days == 1 for r in one)


@pytest.mark.parametrize(
    "changes",
    [
        {"products": [Product.NOAA20_SP]},
        {"products": [Product.NOAA20_NRT, Product.NOAA20_NRT]},
        {"chunk_days": 0},
        {"chunk_days": 6},
        {"as_of": "2025-06-12T12:00:00"},
        {"request_key": "unsafe/key"},
    ],
)
def test_invalid_refresh_policy(changes):
    with pytest.raises(ValueError):
        RefreshSpec.model_validate(dict(region="test", as_of=NOW) | changes)


def test_stable_identity_and_key_conflict_identity():
    region = uuid4()
    first = RefreshSpec(region="test", as_of=NOW)
    assert first.identity(region) == RefreshSpec.model_validate(first.model_dump()).identity(region)
    keyed = RefreshSpec(region="test", as_of=NOW, request_key="same")
    newer = RefreshSpec(region="test", as_of=NOW + timedelta(minutes=1), request_key="same")
    assert keyed.identity(region) == newer.identity(region)
    assert first.identity(region) != first.identity(uuid4())


def test_failed_sensor_counts_are_unknown_and_empty_success_is_known_zero():
    spec = RefreshSpec(region="test", as_of=NOW)
    chunks = [RefreshChunk(key=str(i), request=r) for i, r in enumerate(spec.requests(BOX))]
    chunks[0].status = "succeeded"
    chunks[0].counts = RefreshCounts(fetched=0, accepted=0, rejected=0, duplicate=0)
    chunks[0].completed_at = NOW
    chunks[1].status = "failed"
    state = RefreshState(
        refresh_id=uuid4(), region_id=uuid4(), bbox=BOX, spec=spec, updated_at=NOW, chunks=chunks
    )
    summary = summarize_refresh(state)
    assert summary.coverage == "partial"
    assert summary.products[0].counts.fetched == 0
    assert summary.products[1].counts is None
    assert summary.products[1].coverage == "unavailable"
