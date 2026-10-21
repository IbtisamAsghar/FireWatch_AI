"""Real PostgreSQL acceptance for FIRMS upserts, revisions and reproducible run inputs."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from firms_factory import NOW, ROW, raw_batch
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_schema import T, region, running

from firewatch.db.models import Detection, DetectionSource, IngestionBatch, SourceAsset
from firewatch.repositories.ingestion import persist_normalized
from firewatch.repositories.revisions import RevisionPolicy, freeze_firms_inputs, select_revisions
from firewatch.schemas.common import Product, Sensor
from firewatch.sources.firms_normalize import normalize_firms_batch

pytestmark = pytest.mark.integration
START = T.replace(hour=0)
END = START + timedelta(days=1)


def policy(*saved, products=None, cutoff=NOW + timedelta(days=10), **kw):
    return RevisionPolicy(
        products=products or {Sensor.NOAA20: Product.NOAA20_NRT},
        batch_ids=[item.batch_id for item in saved],
        retrieval_cutoff=cutoff,
        **kw,
    )


def ingest(session, tmp_path, area, rows=None, **kw):
    normalized = normalize_firms_batch(raw_batch(tmp_path, rows, **kw))
    return normalized, persist_normalized(session, normalized, area.id)


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_repeat_and_independent_fetches_use_database_uniqueness(session, tmp_path):
    area = region(session)
    normalized, first = ingest(session, tmp_path, area, [dict(ROW), dict(ROW), ROW | {"frp": "18"}])
    assert first.inserted_count == 1 and first.duplicate_count == 2
    again = persist_normalized(session, normalized, area.id)
    assert again.reused and again.inserted_count == 0
    _, second = ingest(session, tmp_path, area, retrieved=NOW + timedelta(hours=1))
    assert second.inserted_count == 0 and second.duplicate_count == 1
    assert count(session, Detection) == 1
    assert count(session, DetectionSource) == count(session, SourceAsset) == 2
    source = session.scalar(
        select(DetectionSource).where(DetectionSource.batch_id == first.batch_id)
    )
    assert len(source.raw_values["revisions"]) == 2


def test_corrected_revision_selected_without_changing_old_inputs(session, tmp_path):
    area = region(session)
    _, first = ingest(session, tmp_path, area)
    run = running(session, area)
    pinned = freeze_firms_inputs(session, run.id, START, policy(first))
    assert pinned[0].observation.frp_mw == 12.4
    _, corrected = ingest(
        session, tmp_path, area, [ROW | {"frp": "42"}], retrieved=NOW + timedelta(hours=1)
    )
    chosen = select_revisions(session, area.id, START, END, policy(first, corrected))
    assert len(chosen) == 1 and chosen[0].observation.frp_mw == 42
    assert session.scalar(select(Detection)).frp_mw == 12.4
    assert freeze_firms_inputs(session, run.id, START, policy(first)) == pinned
    with pytest.raises(ValueError, match="repinned"):
        freeze_firms_inputs(session, run.id, START, policy(first, corrected))


@pytest.mark.parametrize("replacement", [[], [ROW | {"latitude": "34.2"}]])
def test_complete_later_response_removes_stale_or_moved_rows(session, tmp_path, replacement):
    area = region(session)
    _, first = ingest(session, tmp_path, area)
    _, later = ingest(session, tmp_path, area, replacement, retrieved=NOW + timedelta(hours=1))
    chosen = select_revisions(session, area.id, START, END, policy(first, later))
    assert len(chosen) == len(replacement)
    if chosen:
        assert chosen[0].observation.latitude == 34.2
    assert len(select_revisions(session, area.id, START, END, policy(first))) == 1


def test_explicit_products_separate_satellites_and_science(session, tmp_path):
    area = region(session)
    _, nrt = ingest(session, tmp_path, area)
    _, science = ingest(
        session, tmp_path, area, [ROW | {"version": "2.0"}], product=Product.NOAA20_SP
    )
    _, other = ingest(
        session, tmp_path, area, [ROW | {"satellite": "N21"}], product=Product.NOAA21_NRT
    )
    assert count(session, Detection) == 3
    selected = select_revisions(
        session,
        area.id,
        START,
        END,
        policy(
            science,
            other,
            products={Sensor.NOAA20: Product.NOAA20_SP, Sensor.NOAA21: Product.NOAA21_NRT},
        ),
    )
    assert len(selected) == 2
    assert selected[0].physical_key != selected[1].physical_key
    with pytest.raises(ValueError, match="policy"):
        select_revisions(session, area.id, START, END, policy(nrt, science))
    with pytest.raises(ValueError):
        policy(nrt, products={Sensor.NOAA21: Product.NOAA20_NRT})


def test_availability_quality_and_wrong_region_fail_closed(session, tmp_path):
    area = region(session)
    _, first = ingest(session, tmp_path, area)
    with pytest.raises(ValueError, match="policy"):
        select_revisions(
            session, area.id, START, END, policy(first, cutoff=NOW - timedelta(seconds=1))
        )
    other = region(session)
    with pytest.raises(ValueError, match="policy"):
        select_revisions(session, other.id, START, END, policy(first))
    _, partial = ingest(session, tmp_path, area, [dict(ROW), ROW | {"frp": "bad"}])
    with pytest.raises(ValueError, match="policy"):
        select_revisions(session, area.id, START, END, policy(partial))
    assert (
        len(
            select_revisions(
                session, area.id, START, END, policy(partial, allow_rejected_batches=True)
            )
        )
        == 1
    )


def test_overlapping_regions_share_detection_without_losing_lineage(session, tmp_path):
    area = region(session)
    _, first = ingest(session, tmp_path, area)
    other = region(session)
    other.east = 73
    session.flush()
    _, second = ingest(session, tmp_path, other, bbox=dict(west=70, south=33, east=73, north=36))
    assert second.inserted_count == 0
    assert count(session, Detection) == 1
    assert count(session, DetectionSource) == 2
    assert len(select_revisions(session, area.id, START, END, policy(first))) == 1


def test_tampering_and_region_mismatch_rejected(session, tmp_path):
    area = region(session)
    raw = raw_batch(tmp_path)
    normalized = normalize_firms_batch(raw)
    normalized.revisions[0].observation.frp_mw = 100
    with pytest.raises(ValueError, match="digest"):
        persist_normalized(session, normalized, area.id)
    assert count(session, SourceAsset) == 0
    area.east = 73
    with pytest.raises(ValueError, match="bbox"):
        persist_normalized(session, normalize_firms_batch(raw), area.id)


def test_concurrent_upserts_and_rollback(schema_engine, tmp_path):
    with Session(schema_engine) as session, session.begin():
        area = region(session)
        area_id = area.id
    raws = [normalize_firms_batch(raw_batch(tmp_path)) for _ in range(2)]

    def worker(raw):
        with Session(schema_engine) as session, session.begin():
            return persist_normalized(session, raw, area_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, raws))
    assert sum(r.inserted_count for r in results) == 1
    with Session(schema_engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(DetectionSource)
                .where(DetectionSource.batch_id.in_([r.batch_id for r in results]))
            )
            == 2
        )
    raw = normalize_firms_batch(raw_batch(tmp_path, [ROW | {"latitude": "34.3"}]))
    with pytest.raises(RuntimeError):
        with Session(schema_engine) as session, session.begin():
            saved = persist_normalized(session, raw, area_id)
            raise RuntimeError("rollback")
    with Session(schema_engine) as session:
        assert session.get(IngestionBatch, saved.batch_id) is None
        assert session.get(SourceAsset, saved.asset_id) is None


def test_completed_run_keeps_pins_and_detects_source_tampering(session, tmp_path):
    from firewatch.repositories.runs import complete_and_publish_run

    area = region(session)
    _, saved = ingest(session, tmp_path, area)
    run = running(session, area)
    selected = freeze_firms_inputs(session, run.id, START, policy(saved))
    complete_and_publish_run(session, run.id, completed_at=T + timedelta(hours=1))
    assert freeze_firms_inputs(session, run.id, START, policy(saved)) == selected
    source = session.scalar(
        select(DetectionSource).where(DetectionSource.batch_id == saved.batch_id)
    )
    import copy

    changed = copy.deepcopy(source.raw_values)
    changed["revisions"][0]["observation"]["frp_mw"] = 999
    source.raw_values = changed
    session.flush()
    with pytest.raises(ValueError, match="integrity"):
        freeze_firms_inputs(session, run.id, START, policy(saved))
