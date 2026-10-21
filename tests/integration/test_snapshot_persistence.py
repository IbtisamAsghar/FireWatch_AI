"""Snapshot persistence and source-pin acceptance against real PostgreSQL schemas."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from conftest import migrate
from firms_factory import NOW, ROW, raw_batch
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_schema import T, region, running

from firewatch.db.models import ClusterMember, ClusterSnapshotRecord, LiveRunHead, Run
from firewatch.processing.clusters import ClusteringSpec
from firewatch.repositories.ingestion import persist_normalized
from firewatch.repositories.snapshots import build_run_snapshots, policy_for_snapshots
from firewatch.schemas.common import Product, Sensor, Stage
from firewatch.sources.firms_normalize import normalize_firms_batch

pytestmark = pytest.mark.integration


def ingest(session, tmp_path, area, rows, *, prior=False, retrieved=NOW):
    raw = raw_batch(tmp_path, rows, retrieved=retrieved)
    if prior:
        raw.request = raw.request.model_copy(update={"start_date": T.date() - timedelta(days=1)})
        raw.asset.request_metadata = raw.request.model_dump(mode="json")
        manifest = json.loads(raw.manifest_path.read_text())
        manifest["request"] = raw.request.model_dump(mode="json")
        manifest["asset"] = raw.asset.model_dump(mode="json")
        raw.manifest_path.write_text(json.dumps(manifest))
    return persist_normalized(session, normalize_firms_batch(raw), area.id)


def setup_sources(session, tmp_path, *, empty=False):
    area = region(session)
    ingest(
        session,
        tmp_path,
        area,
        [] if empty else [ROW | {"acq_date": "2025-06-09", "acq_time": "1300"}],
        prior=True,
    )
    ingest(
        session,
        tmp_path,
        area,
        []
        if empty
        else [
            ROW | {"acq_time": "1100"},
            ROW | {"acq_time": "0500"},
            ROW | {"acq_time": "1201", "frp": "9999"},
        ],
    )
    run = running(session, area)
    run.stage_status = {}
    session.flush()
    policy = policy_for_snapshots(
        session, area.id, T, {Sensor.NOAA20: Product.NOAA20_NRT}, NOW + timedelta(days=2)
    )
    return area, run, policy


def test_persist_retry_activity_and_future_exclusion(session, tmp_path):
    _, run, policy = setup_sources(session, tmp_path)
    first = build_run_snapshots(session, run.id, policy)
    second = build_run_snapshots(session, run.id, policy)
    assert first == second and first.candidate_count == 1 and first.history_count == 3
    assert first.snapshots[0].activity_counts == {
        "detections_6h": 1,
        "detections_12h": 2,
        "detections_24h": 3,
    }
    assert session.scalar(select(func.count()).select_from(ClusterSnapshotRecord)) == 1
    assert session.scalar(select(func.count()).select_from(ClusterMember)) == 1
    assert run.stage_status[Stage.SNAPSHOTS.value] == "succeeded"
    assert (
        run.status == "running"
        and session.scalar(select(func.count()).select_from(LiveRunHead)) == 0
    )


def test_corrected_source_cannot_change_saved_run(session, tmp_path):
    area, run, policy = setup_sources(session, tmp_path)
    first = build_run_snapshots(session, run.id, policy)
    ingest(
        session,
        tmp_path,
        area,
        [ROW | {"acq_time": "1100", "frp": "120"}],
        retrieved=NOW + timedelta(hours=1),
    )
    assert build_run_snapshots(session, run.id, policy) == first
    new_policy = policy_for_snapshots(
        session, area.id, T, {Sensor.NOAA20: Product.NOAA20_NRT}, NOW + timedelta(days=2)
    )
    with pytest.raises(ValueError, match="repinned"):
        build_run_snapshots(session, run.id, new_policy)
    other = running(session, area)
    changed = build_run_snapshots(session, other.id, new_policy)
    assert changed.snapshots[0].snapshot.frp_max_mw == 120
    assert first.snapshots[0].snapshot.frp_max_mw == 12.4


def test_empty_stage_is_explicit_and_idempotent(session, tmp_path):
    _, run, policy = setup_sources(session, tmp_path, empty=True)
    first = build_run_snapshots(session, run.id, policy)
    assert (
        first.snapshots == []
        and run.input_asset_manifest["clustering"]["batch"]["candidate_count"] == 0
    )
    assert build_run_snapshots(session, run.id, policy) == first
    assert session.scalar(select(func.count()).select_from(ClusterSnapshotRecord)) == 0


def test_missing_source_day_is_unknown_not_empty(session, tmp_path):
    area = region(session)
    ingest(session, tmp_path, area, [])
    with pytest.raises(ValueError, match="coverage"):
        policy_for_snapshots(session, area.id, T, {Sensor.NOAA20: Product.NOAA20_NRT}, NOW)


def test_configuration_and_materialized_tampering_refused(session, tmp_path):
    _, run, policy = setup_sources(session, tmp_path)
    batch = build_run_snapshots(session, run.id, policy)
    with pytest.raises(ValueError, match="configuration"):
        build_run_snapshots(
            session, run.id, policy, ClusteringSpec(algorithm_version="dbscan-3km-v2", radius_km=3)
        )
    row = session.get(ClusterSnapshotRecord, batch.snapshots[0].snapshot.snapshot_id)
    row.frp_sum_mw = 999
    session.flush()
    with pytest.raises(ValueError, match="values"):
        build_run_snapshots(session, run.id, policy)


def test_snapshot_stage_rolls_back_pins_members_and_manifest(session, tmp_path):
    _, run, policy = setup_sources(session, tmp_path)
    identifier = run.id
    with pytest.raises(RuntimeError), session.begin_nested():
        build_run_snapshots(session, identifier, policy)
        raise RuntimeError("simulated stage interruption")
    session.expire_all()
    saved = session.get(Run, identifier)
    assert (
        "firms" not in saved.input_asset_manifest and "clustering" not in saved.input_asset_manifest
    )
    assert session.scalar(select(func.count()).select_from(ClusterSnapshotRecord)) == 0
    assert session.scalar(select(func.count()).select_from(ClusterMember)) == 0
    assert len(build_run_snapshots(session, identifier, policy).snapshots) == 1


def test_concurrent_same_run_is_idempotent(empty_engine, tmp_path):
    migrate(empty_engine)
    with Session(empty_engine) as session, session.begin():
        _, run, policy = setup_sources(session, tmp_path)
        identifier = run.id

    def execute(_):
        with Session(empty_engine) as session, session.begin():
            return build_run_snapshots(session, identifier, policy)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(execute, range(2)))
    assert first == second
    with Session(empty_engine) as session:
        assert session.scalar(select(func.count()).select_from(ClusterSnapshotRecord)) == 1
        assert session.scalar(select(func.count()).select_from(ClusterMember)) == 1


def test_saved_manifest_tampering_is_rejected(session, tmp_path):
    from copy import deepcopy

    _, run, policy = setup_sources(session, tmp_path)
    build_run_snapshots(session, run.id, policy)
    changed = deepcopy(run.input_asset_manifest)
    changed["clustering"]["batch"]["candidate_count"] = 999
    run.input_asset_manifest = changed
    session.flush()
    with pytest.raises(ValueError, match="manifest changed"):
        build_run_snapshots(session, run.id, policy)
