"""Real PostgreSQL archive checkpoint/publication acceptance; synthetic CSV only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from uuid import UUID

import pytest
from archive_factory import archive
from conftest import migrate
from firms_factory import ROW
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firewatch.db.models import Detection, DetectionSource, IngestionBatch, Region, SourceAsset
from firewatch.repositories.archive import import_prepared
from firewatch.repositories.revisions import RevisionPolicy, select_revisions
from firewatch.schemas.common import JobStatus, Sensor
from firewatch.sources.archive_manifest import ArchiveError
from firewatch.sources.archive_stage import prepare_archive

pytestmark = pytest.mark.integration


@pytest.fixture
def archive_engine(empty_engine):
    migrate(empty_engine)
    with Session(empty_engine) as session, session.begin():
        session.add(Region(name="archive-test", west=70, south=33, east=74, north=36))
    return empty_engine


def prepare(tmp_path, rows=None, **changes):
    raw, manifest = archive(tmp_path, rows, **changes)
    result = prepare_archive(Path(raw.asset.storage_path), manifest, tmp_path / "out", chunk_rows=1)
    return result


def selected(engine, *reports):
    ids = [UUID(item) for report in reports for item in report["source_batch_ids"]]
    with Session(engine) as session:
        area = session.scalar(select(Region).where(Region.name == "archive-test"))
        return select_revisions(
            session,
            area.id,
            datetime(2025, 6, 9, tzinfo=UTC),
            datetime(2025, 6, 12, tzinfo=UTC),
            RevisionPolicy(
                products={Sensor.NOAA20: "VIIRS_NOAA20_NRT"},
                batch_ids=ids,
                retrieval_cutoff=datetime(2025, 6, 12, tzinfo=UTC),
            ),
        )


def test_all_chunks_publish_as_one_day_and_reimport_is_idempotent(archive_engine, tmp_path):
    rows = [ROW | {"latitude": str(34 + i / 1000)} for i in range(7)]
    prepared = prepare(tmp_path, rows)
    report = import_prepared(prepared, archive_engine, chunk_rows=2)
    assert report["database"]["inserted_observations_this_run"] == 7
    assert len(selected(archive_engine, report)) == 7
    again = import_prepared(prepared, archive_engine, chunk_rows=3)
    assert again["database"]["reused"]
    assert again["database"]["inserted_observations_this_run"] == 0
    with Session(archive_engine) as session:
        assert session.scalar(select(func.count()).select_from(Detection)) == 7
        assert session.scalar(select(func.count()).select_from(IngestionBatch)) == 3
    assert prepared.quality["training_classes"]["positive"] is None


def test_duplicate_variants_across_chunks_preserve_history(archive_engine, tmp_path):
    prepared = prepare(tmp_path, [dict(ROW)] * 4 + [ROW | {"frp": "99"}])
    report = import_prepared(prepared, archive_engine, chunk_rows=1)
    assert len(selected(archive_engine, report)) == 1
    with Session(archive_engine) as session:
        source = session.scalar(select(DetectionSource))
        assert len(source.raw_values["revisions"]) == 2
        assert sum(source.raw_values["revision_occurrences"].values()) == 5
        batch = session.get(IngestionBatch, source.batch_id)
        assert (batch.fetched_count, batch.accepted_count, batch.duplicate_count) == (5, 1, 4)


def test_interrupted_chunk_rolls_back_and_resume_uses_database_checkpoint(
    archive_engine, tmp_path, monkeypatch
):
    import firewatch.repositories.archive as repository

    prepared = prepare(tmp_path, [ROW | {"latitude": str(34 + i / 100)} for i in range(4)])
    original = repository._write_revision
    calls = 0

    def interrupt(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Simulated interruption")
        return original(*args)

    monkeypatch.setattr(repository, "_write_revision", interrupt)
    with pytest.raises(RuntimeError, match="Simulated"):
        import_prepared(prepared, archive_engine, chunk_rows=1)
    with Session(archive_engine) as session:
        parent = session.get(SourceAsset, prepared.import_id)
        assert parent.request_metadata["progress"]["revisions"] == 1
        batch = session.scalar(
            select(IngestionBatch).where(IngestionBatch.status == JobStatus.RUNNING)
        )
        assert batch.accepted_count == 1
        assert session.scalar(select(func.count()).select_from(Detection)) == 1
        area = session.scalar(select(Region))
        with pytest.raises(ValueError, match="policy"):
            select_revisions(
                session,
                area.id,
                datetime(2025, 6, 9, tzinfo=UTC),
                datetime(2025, 6, 12, tzinfo=UTC),
                RevisionPolicy(
                    products={Sensor.NOAA20: "VIIRS_NOAA20_NRT"},
                    batch_ids=[batch.id],
                    retrieval_cutoff=datetime(2025, 6, 12, tzinfo=UTC),
                ),
            )
    monkeypatch.setattr(repository, "_write_revision", original)
    # Rebuild staging as a fresh CLI process would, rather than trusting local progress JSON.
    prepared = prepare_archive(
        prepared.folder / "original.csv", prepared.manifest, tmp_path / "out", chunk_rows=2
    )
    report = import_prepared(prepared, archive_engine, chunk_rows=2)
    assert report["database"]["inserted_observations_total"] == 4
    assert report["database"]["inserted_observations_this_run"] == 3
    assert len(selected(archive_engine, report)) == 4


def test_later_archive_replaces_entire_day_without_unioning_old_geometry(archive_engine, tmp_path):
    first = import_prepared(prepare(tmp_path), archive_engine)
    second = prepare(tmp_path, [ROW | {"latitude": "34.6"}], retrieved_at="2025-06-11T01:00:00Z")
    later = import_prepared(second, archive_engine)
    result = selected(archive_engine, first, later)
    assert len(result) == 1 and result[0].observation.latitude == 34.6


def test_missing_coverage_days_are_not_selectable(archive_engine, tmp_path):
    prepared = prepare(tmp_path, covered_intervals=[])
    report = import_prepared(prepared, archive_engine)
    assert report["source_batch_ids"] == []
    assert not report["member_4_handoff"]["observation_history_complete"]
    with Session(archive_engine) as session:
        assert all(
            status == JobStatus.FAILED for status in session.scalars(select(IngestionBatch.status))
        )


def test_archive_owner_lock_prevents_two_checkpoint_writers(archive_engine, tmp_path, monkeypatch):
    import firewatch.repositories.archive as repository

    prepared = prepare(tmp_path)
    started = Event()
    release = Event()
    original = repository._write_revision

    def pause(*args):
        started.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(repository, "_write_revision", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(import_prepared, prepared, archive_engine)
        assert started.wait(10)
        try:
            with pytest.raises(ArchiveError, match="already"):
                import_prepared(prepared, archive_engine)
        finally:
            release.set()
        assert task.result()["database"]["inserted_observations_total"] == 1


def test_wrong_region_fails_before_writing_assets(archive_engine, tmp_path):
    prepared = prepare(tmp_path, region="missing-region")
    with pytest.raises(ArchiveError, match="region"):
        import_prepared(prepared, archive_engine)
    with Session(archive_engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceAsset)) == 0


def test_confirmed_empty_archive_can_be_selected_without_invented_rows(archive_engine, tmp_path):
    prepared = prepare(
        tmp_path,
        [],
        confirmed_empty_intervals=[dict(start_date="2025-06-09", end_date="2025-06-11")],
    )
    report = import_prepared(prepared, archive_engine, chunk_rows=1)
    assert len(report["source_batch_ids"]) == 3
    assert selected(archive_engine, report) == []
    assert report["inventory"]["training_classes"]["positive"] is None


def test_unexpected_empty_days_fail_closed(archive_engine, tmp_path):
    prepared = prepare(
        tmp_path,
        [],
        expected_active_intervals=[dict(start_date="2025-06-09", end_date="2025-06-11")],
    )
    report = import_prepared(prepared, archive_engine)
    assert report["source_batch_ids"] == []
    assert not report["member_4_handoff"]["observation_history_complete"]


def test_import_works_with_single_connection_pool(archive_engine, tmp_path):
    from sqlalchemy import create_engine, text

    with archive_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    assert schema.startswith("fw_test_")
    engine = create_engine(
        archive_engine.url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )
    try:
        report = import_prepared(prepare(tmp_path), engine, chunk_rows=1)
        assert report["database"]["inserted_observations_this_run"] == 1
    finally:
        engine.dispose()
