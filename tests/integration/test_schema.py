"""Database acceptance: real migrations, direct-SQL guards and publication rollback."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from firewatch.db.base import Base
from firewatch.db.models import (
    Alert,
    ClusterMember,
    ClusterSnapshotRecord,
    Detection,
    DetectionRegion,
    DetectionSource,
    FeatureSetRecord,
    IngestionBatch,
    LiveRunHead,
    ModelVersion,
    Region,
    Run,
    Score,
    SourceAsset,
)
from firewatch.repositories.detections import save_detection
from firewatch.repositories.regions import load_regions, seed_regions
from firewatch.repositories.runs import complete_and_publish_run
from firewatch.schemas.common import (
    Completeness,
    Confidence,
    JobStatus,
    ModelStatus,
    Priority,
    Product,
    Provider,
    RunMode,
    RunStatus,
    ScoreKind,
    Sensor,
    Stage,
)
from firewatch.schemas.contracts import DetectionInput

pytestmark = pytest.mark.integration
T = datetime(2025, 6, 10, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def migration(engine, target="head", downgrade=False, check=False):
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if check:
            command.check(config)
        elif downgrade:
            command.downgrade(config, target)
        else:
            command.upgrade(config, target)


def region(session, name=None):
    record = Region(name=name or "test-" + uuid4().hex, west=70, east=74, south=33, north=36)
    session.add(record)
    session.flush()
    return record


def running(session, area=None, cutoff=T, mode=RunMode.LIVE):
    area = area if area is not None else region(session)
    record = Run(
        region_id=area.id,
        mode=mode,
        as_of=cutoff,
        status=RunStatus.RUNNING,
        config_hash="a" * 64,
        run_key=uuid4().hex,
        completeness=Completeness.COMPLETE,
        stage_status={stage.value: "succeeded" for stage in Stage},
        scorer_version="baseline-v1",
        feature_version="features-v1",
        started_at=T,
    )
    session.add(record)
    session.flush()
    return record


def batch(session, area, product=Product.NOAA20_NRT):
    asset = SourceAsset(
        provider=Provider.FIRMS,
        product=product,
        request_metadata={},
        retrieved_at=T,
        storage_path="synthetic-test.csv",
        content_hash="a" * 64,
        format="csv",
        processing_version="normalizer-v1",
    )
    session.add(asset)
    session.flush()
    record = IngestionBatch(
        region_id=area.id,
        product=product,
        window_start=T - timedelta(hours=4),
        window_end=T + timedelta(hours=4),
        status=JobStatus.SUCCEEDED,
        asset_id=asset.id,
        fetched_count=1,
        accepted_count=1,
    )
    session.add(record)
    session.flush()
    return record


def observation(**overrides):
    values = dict(
        product=Product.NOAA20_NRT,
        sensor=Sensor.NOAA20,
        latitude=34.0,
        longitude=71.0,
        acquired_at=T - timedelta(hours=1),
        frp_mw=None,
        confidence=Confidence.UNKNOWN,
        raw_values={"frp": "invalid-provider-value"},
        quality_flags=["frp_conversion_failed"],
        dedup_key=uuid4().hex * 2,
    )
    return DetectionInput(**(values | overrides))


def outputs(session, run, priority=Priority.HIGH):
    area = session.get(Region, run.region_id)
    source = batch(session, area)
    detection = save_detection(session, source.id, area.id, observation())
    snapshot = ClusterSnapshotRecord(
        run_id=run.id,
        latitude=34,
        longitude=71,
        member_count=1,
        latest_detection_at=detection.acquired_at,
        algorithm_version="cluster-v1",
    )
    session.add(snapshot)
    session.flush()
    session.add(ClusterMember(snapshot_id=snapshot.id, detection_id=detection.id))
    session.add(
        FeatureSetRecord(
            snapshot_id=snapshot.id,
            run_id=run.id,
            feature_version="features-v1",
            values={"frp_max_mw": None},
            missing_flags={"frp_max_mw": True},
            units={},
            lineage={},
            feature_hash="a" * 64,
        )
    )
    score = Score(
        snapshot_id=snapshot.id,
        run_id=run.id,
        scorer_version="baseline-v1",
        score_kind=ScoreKind.BASELINE,
        priority_score=0.7,
        priority=priority,
        explanation={},
        scored_at=T + timedelta(hours=4),
    )
    session.add(score)
    session.flush()
    return snapshot, score


def alert(session, score):
    payload = b'{"fixture":"synthetic"}'
    record = Alert(
        snapshot_id=score.snapshot_id,
        run_id=score.run_id,
        score_id=score.id,
        canonical_payload=payload,
        original_hash=hashlib.sha256(payload).hexdigest(),
        evidence_schema_version="evidence-v1",
    )
    session.add(record)
    session.flush()
    return record


def test_empty_upgrade_and_no_model_drift(empty_engine):
    migration(empty_engine)
    assert set(Base.metadata.tables) <= set(inspect(empty_engine).get_table_names())
    migration(empty_engine, check=True)
    migration(empty_engine, "base", downgrade=True)
    assert set(inspect(empty_engine).get_table_names()) == {"alembic_version"}
    migration(empty_engine)
    migration(empty_engine, check=True)


def test_upgrade_previous_revision_preserves_sample_data(empty_engine):
    migration(empty_engine, "0001")
    with Session(empty_engine) as session:
        area = region(session)
        source = batch(session, area)
        detection = save_detection(session, source.id, area.id, observation())
        identifier = detection.id
        session.commit()
    migration(empty_engine)
    with Session(empty_engine) as session:
        assert session.get(Detection, identifier).raw_values["frp"] == "invalid-provider-value"
    migration(empty_engine, "0001", downgrade=True)
    with Session(empty_engine) as session:
        assert session.get(Detection, identifier).frp_mw is None
    migration(empty_engine)


def test_seed_idempotent_updates_preserve_uuid(session):
    configuration = load_regions(ROOT / "configs/regions.yaml")
    first = seed_regions(session, configuration)
    assert seed_regions(session, configuration) == first
    updated = configuration.model_copy(deep=True)
    updated.regions[0].description = "reviewed description"
    assert seed_regions(session, updated) == first
    assert session.get(Region, first[0]).description == "reviewed description"
    assert session.get(Region, first[0]).enabled is False


def test_overlapping_regions_share_observation_and_retain_source_lineage(session):
    first, second = region(session), region(session)
    original = observation()
    one = save_detection(session, batch(session, first).id, first.id, original)
    two_batch = batch(session, second)
    two = save_detection(session, two_batch.id, second.id, original)
    again = save_detection(session, two_batch.id, second.id, original)
    assert one.id == two.id == again.id
    assert session.scalar(select(func.count()).select_from(Detection)) == 1
    assert session.scalar(select(func.count()).select_from(DetectionSource)) == 2
    assert session.scalar(select(func.count()).select_from(DetectionRegion)) == 2
    assert two.frp_mw is None and two.raw_values == original.raw_values


def test_dedup_identity_collision_rejected(session):
    area = region(session)
    source = batch(session, area)
    original = observation()
    save_detection(session, source.id, area.id, original)
    with pytest.raises(ValueError, match="collides"):
        save_detection(session, source.id, area.id, original.model_copy(update={"latitude": 35.0}))


@pytest.mark.parametrize("field,value", [("west", 75), ("south", 91), ("north", -95)])
def test_database_rejects_invalid_region_coordinates(session, field, value):
    area = region(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(update(Region).where(Region.id == area.id).values({field: value}))


def test_publication_requires_stage_completion(session):
    run = running(session)
    run.stage_status = {"ingest": "succeeded"}
    session.flush()
    with pytest.raises(ValueError, match="stages"):
        complete_and_publish_run(session, run.id)
    assert session.get(LiveRunHead, run.region_id) is None
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            update(Run)
            .where(Run.id == run.id)
            .values(status=RunStatus.COMPLETED, completed_at=T + timedelta(hours=5))
        )


def test_empty_complete_live_run_can_publish_and_retry_is_idempotent(session):
    run = running(session)
    complete_and_publish_run(session, run.id, T + timedelta(hours=5))
    assert session.get(LiveRunHead, run.region_id).run_id == run.id
    assert complete_and_publish_run(session, run.id).status == RunStatus.COMPLETED


def test_replay_never_changes_live_head_and_older_live_cannot_replace_newer(session):
    area = region(session)
    latest = running(session, area, cutoff=T + timedelta(hours=2))
    complete_and_publish_run(session, latest.id, T + timedelta(hours=6))
    replay = running(session, area, mode=RunMode.REPLAY)
    complete_and_publish_run(session, replay.id, T + timedelta(hours=6))
    older = running(session, area)
    complete_and_publish_run(session, older.id, T + timedelta(hours=6))
    assert session.get(LiveRunHead, area.id).run_id == latest.id
    for invalid in (replay, older):
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                update(LiveRunHead)
                .where(LiveRunHead.region_id == area.id)
                .values(run_id=invalid.id)
            )


def test_raw_sql_incomplete_run_cannot_become_live_head(session):
    run = running(session)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(LiveRunHead(region_id=run.region_id, run_id=run.id))
        session.flush()


def test_incomplete_outputs_block_completion_and_head(session):
    run = running(session)
    snapshot, _ = outputs(session, run)
    session.execute(text("DELETE FROM feature_sets WHERE snapshot_id=:id"), {"id": snapshot.id})
    with pytest.raises(IntegrityError), session.begin_nested():
        complete_and_publish_run(session, run.id, T + timedelta(hours=6))
    assert session.get(LiveRunHead, run.region_id) is None
    assert session.get(Run, run.id).status == RunStatus.RUNNING


def test_cross_run_feature_and_score_references_rejected(session):
    run, other = running(session), running(session)
    snapshot, score = outputs(session, run)
    for table in ("feature_sets", "scores"):
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text(f"UPDATE {table} SET run_id=:other WHERE snapshot_id=:snapshot"),
                {"other": other.id, "snapshot": snapshot.id},
            )
    assert score.run_id == run.id


def test_unvalidated_score_cannot_publish_probability(session):
    run = running(session)
    _, score = outputs(session, run)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            update(Score).where(Score.id == score.id).values(escalation_probability=0.9)
        )
    model = ModelVersion(
        version="model-v1",
        artifact_path="synthetic.joblib",
        artifact_hash="a" * 64,
        feature_version="features-v1",
        training_manifest={},
        metrics={},
        target_definition={},
        status=ModelStatus.APPROVED,
        probability_supported=False,
    )
    session.add(model)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            update(Score)
            .where(Score.id == score.id)
            .values(
                scorer_version=model.version,
                model_id=model.id,
                score_kind=ScoreKind.PROBABILITY,
                escalation_probability=0.9,
            )
        )


def test_critical_scores_require_evidence_and_saved_alert_is_immutable(session):
    run = running(session)
    _, score = outputs(session, run, Priority.CRITICAL)
    with pytest.raises(IntegrityError), session.begin_nested():
        complete_and_publish_run(session, run.id, T + timedelta(hours=6))
    evidence = alert(session, score)
    for statement in (
        "UPDATE alerts SET evidence_schema_version='changed' WHERE id=:id",
        "DELETE FROM alerts WHERE id=:id",
        "TRUNCATE alerts CASCADE",
    ):
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(text(statement), {"id": evidence.id})
    complete_and_publish_run(session, run.id, T + timedelta(hours=6))
    assert session.get(LiveRunHead, run.region_id).run_id == run.id


def test_alert_digest_must_match_exact_bytes(session):
    run = running(session)
    _, score = outputs(session, run)
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            Alert(
                run_id=run.id,
                snapshot_id=score.snapshot_id,
                score_id=score.id,
                canonical_payload=b"changed",
                original_hash="a" * 64,
                evidence_schema_version="v1",
            )
        )
        session.flush()


def test_completed_run_outputs_cannot_change_or_be_truncated(session):
    run = running(session)
    snapshot, score = outputs(session, run)
    complete_and_publish_run(session, run.id, T + timedelta(hours=6))
    operations = [
        ("UPDATE runs SET status='running' WHERE id=:id", run.id),
        ("UPDATE scores SET priority_score=0.1 WHERE id=:id", score.id),
        ("DELETE FROM feature_sets WHERE snapshot_id=:id", snapshot.id),
        ("DELETE FROM cluster_members WHERE snapshot_id=:id", snapshot.id),
        ("TRUNCATE cluster_snapshots CASCADE", snapshot.id),
    ]
    for sql, identifier in operations:
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(text(sql), {"id": identifier})


def test_future_observation_cannot_join_snapshot(session):
    run = running(session)
    area = session.get(Region, run.region_id)
    record = save_detection(
        session, batch(session, area).id, area.id, observation(acquired_at=T + timedelta(hours=1))
    )
    snapshot = ClusterSnapshotRecord(
        run_id=run.id,
        latitude=34,
        longitude=71,
        member_count=1,
        latest_detection_at=T,
        algorithm_version="v1",
    )
    session.add(snapshot)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(ClusterMember(snapshot_id=snapshot.id, detection_id=record.id))
        session.flush()


def test_two_publishers_serialize_without_regressing_head(schema_engine):
    factory = sessionmaker(schema_engine, expire_on_commit=False)
    with factory.begin() as session:
        area = region(session)
        area_id = area.id
        first = running(session, area)
        second = running(session, area, cutoff=T + timedelta(hours=1))
        ids = first.id, second.id

    def publish(identifier):
        with factory.begin() as session:
            complete_and_publish_run(session, identifier, T + timedelta(hours=6))

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(publish, ids))
    with factory() as session:
        assert session.get(LiveRunHead, area_id).run_id == ids[1]


def test_publication_and_completion_roll_back_together(schema_engine):
    factory = sessionmaker(schema_engine, expire_on_commit=False)
    with factory.begin() as session:
        run = running(session)
        run_id, area_id = run.id, run.region_id
    with pytest.raises(RuntimeError), factory.begin() as session:
        complete_and_publish_run(session, run_id, T + timedelta(hours=5))
        raise RuntimeError("Simulate failed transaction after publication")
    with factory() as session:
        assert session.get(LiveRunHead, area_id) is None
        assert session.get(Run, run_id).status == RunStatus.RUNNING


def test_publication_preserves_pending_checkpoint_when_autoflush_is_disabled(session):
    run = running(session)
    run.stage_status = {stage.value: "pending" for stage in Stage}
    session.flush()
    session.autoflush = False
    run.stage_status = {stage.value: "succeeded" for stage in Stage}
    complete_and_publish_run(session, run.id, T + timedelta(hours=5))
    assert session.get(LiveRunHead, run.region_id).run_id == run.id


def test_failed_refresh_preserves_existing_latest_live(schema_engine):
    factory = sessionmaker(schema_engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as session:
        area = region(session)
        previous = running(session, area)
        complete_and_publish_run(session, previous.id, T + timedelta(hours=6))
        refresh = running(session, area, cutoff=T + timedelta(hours=1))
        previous_id, refresh_id, area_id = previous.id, refresh.id, area.id
    with pytest.raises(RuntimeError), factory.begin() as session:
        complete_and_publish_run(session, refresh_id, T + timedelta(hours=6))
        raise RuntimeError("Simulated failed refresh transaction")
    with factory() as session:
        assert session.get(LiveRunHead, area_id).run_id == previous_id
        assert session.get(Run, refresh_id).status == RunStatus.RUNNING


def test_model_feature_version_must_match_the_run(session):
    run = running(session)
    _, score = outputs(session, run)
    model = ModelVersion(
        version="different-features",
        artifact_path="synthetic.joblib",
        artifact_hash="a" * 64,
        feature_version="features-v2",
        training_manifest={},
        metrics={},
        target_definition={},
        status=ModelStatus.APPROVED,
        probability_supported=True,
    )
    session.add(model)
    session.flush()
    with pytest.raises(IntegrityError), session.begin_nested():
        session.execute(
            update(Score)
            .where(Score.id == score.id)
            .values(scorer_version=model.version, model_id=model.id, score_kind=ScoreKind.MODEL)
        )


def test_composite_alert_reference_cannot_point_at_a_different_snapshot(session):
    run, other = running(session), running(session)
    snapshot, score = outputs(session, run)
    other_snapshot, _ = outputs(session, other)
    payload = b"synthetic"
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(
            Alert(
                snapshot_id=other_snapshot.id,
                run_id=run.id,
                score_id=score.id,
                canonical_payload=payload,
                original_hash=hashlib.sha256(payload).hexdigest(),
                evidence_schema_version="v1",
            )
        )
        session.flush()
