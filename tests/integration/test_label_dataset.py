"""Phase 4.1 PostgreSQL selection and portable labels; synthetic provider data only."""

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import migrate
from firms_factory import ROW, raw_batch
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_pipeline_handoff import handoff  # noqa: F401
from test_replay import bundle_source  # noqa: F401

from firewatch.db.models import Detection, IngestionBatch, Region, Run, SourceAsset
from firewatch.ml.dataset import export_labels, verify_label_package
from firewatch.repositories.ingestion import persist_normalized
from firewatch.repositories.labels import select_followup
from firewatch.schemas.common import JobStatus, Product
from firewatch.sources.firms_normalize import normalize_firms_batch

T = datetime(2025, 6, 10, 12, tzinfo=UTC)
AVAILABLE = datetime(2025, 6, 13, tzinfo=UTC)


def ingest_sp(
    session, tmp_path, area, *, tomorrow=False, frp="100", retrieved=AVAILABLE, empty=False
):
    day = "2025-06-11" if tomorrow else "2025-06-10"
    rows = (
        [] if empty else [ROW | {"acq_date": day, "acq_time": "1200", "frp": frp, "version": "2.0"}]
    )
    batch = raw_batch(tmp_path, rows, product=Product.NOAA20_SP, retrieved=retrieved)
    batch.request = batch.request.model_copy(
        update={"start_date": datetime.fromisoformat(day).date()}
    )
    manifest = json.loads(batch.manifest_path.read_bytes())
    manifest["request"] = batch.request.model_dump(mode="json")
    batch.asset.request_metadata = manifest["request"]
    manifest["asset"] = batch.asset.model_dump(mode="json")
    batch.manifest_path.write_text(json.dumps(manifest))
    return persist_normalized(session, normalize_firms_batch(batch), area.id)


@pytest.fixture
def sources(empty_engine, tmp_path):
    migrate(empty_engine)
    with Session(empty_engine) as session, session.begin():
        area = Region(name="label-test", west=70, south=33, east=74, north=36)
        session.add(area)
        session.flush()
        ingest_sp(session, tmp_path, area)
        ingest_sp(session, tmp_path, area, tomorrow=True)
        identifier = area.id
    return empty_engine, identifier


def test_followup_uses_complete_days_and_includes_exact_end_in_read_only_transaction(sources):
    engine, identifier = sources
    with Session(engine) as session, session.begin():
        session.execute(text("SET TRANSACTION READ ONLY"))
        evidence = select_followup(session, identifier, T, AVAILABLE)
    assert len(evidence.source_days) == 2 and all(d.complete for d in evidence.source_days)
    assert any(r.observation.acquired_at == T + timedelta(hours=24) for r in evidence.observations)
    assert evidence.policy.products == {"NOAA-20": "VIIRS_NOAA20_SP"}


def test_later_correction_cannot_change_previously_selected_evidence(sources, tmp_path):
    engine, identifier = sources
    with Session(engine) as session:
        first = select_followup(session, identifier, T, AVAILABLE)
    saved = first.model_dump_json()
    with Session(engine) as session, session.begin():
        area = session.get(Region, identifier)
        ingest_sp(
            session,
            tmp_path,
            area,
            tomorrow=True,
            frp="999",
            retrieved=AVAILABLE + timedelta(days=1),
        )
    with Session(engine) as session:
        assert select_followup(session, identifier, T, AVAILABLE) == first
        changed = select_followup(session, identifier, T, AVAILABLE + timedelta(days=1))
    assert first.model_dump_json() == saved
    assert max(r.observation.frp_mw for r in changed.observations) == 999


@pytest.mark.parametrize("failure", ["failed", "rejected"])
def test_newer_bad_day_does_not_fall_back_to_older_clean_source(sources, tmp_path, failure):
    engine, identifier = sources
    with Session(engine) as session, session.begin():
        area = session.get(Region, identifier)
        ingest_sp(
            session,
            tmp_path,
            area,
            tomorrow=True,
            frp="120",
            retrieved=AVAILABLE + timedelta(hours=1),
        )
        batch = session.scalar(
            select(IngestionBatch).join(SourceAsset).order_by(SourceAsset.retrieved_at.desc())
        )
        if failure == "failed":
            batch.status = JobStatus.FAILED
        else:
            batch.rejected_count = 1
            batch.fetched_count += 1
    with Session(engine) as session:
        evidence = select_followup(session, identifier, T, AVAILABLE + timedelta(hours=2))
    assert not evidence.source_days[-1].complete


def test_empty_day_replaces_older_activity(sources, tmp_path):
    engine, identifier = sources
    with Session(engine) as session, session.begin():
        area = session.get(Region, identifier)
        ingest_sp(
            session,
            tmp_path,
            area,
            tomorrow=True,
            empty=True,
            retrieved=AVAILABLE + timedelta(hours=1),
        )
    with Session(engine) as session:
        evidence = select_followup(session, identifier, T, AVAILABLE + timedelta(hours=2))
    assert all(r.observation.acquired_at <= T for r in evidence.observations)
    assert all(d.complete for d in evidence.source_days)


@pytest.fixture
def label_package(handoff, tmp_path):  # noqa: F811
    engine, _, _, root, verified = handoff
    output = tmp_path / "labels"
    report = export_labels(
        engine,
        root,
        output,
        handoff_sha256=verified["manifest_sha256"],
        spec_path=Path("configs/target-v1.yaml"),
        design_path=Path("configs/target-dataset-v1.yaml"),
        outages_path=Path("configs/label-outages.yaml"),
        retrieval_cutoff=AVAILABLE,
    )
    return engine, root, verified, output, report


def test_label_package_roundtrip_relocation_and_cli_need_no_database(label_package, tmp_path):
    from typer.testing import CliRunner

    from firewatch.cli import app

    _, _, _, root, report = label_package
    assert not report["training_ready"] and report["labels"] == {"unknown": 1}
    verified = verify_label_package(root)
    relocated = tmp_path / "relocated-labels"
    shutil.copytree(root, relocated)
    assert verify_label_package(relocated, verified["manifest_sha256"]) == verified
    result = CliRunner().invoke(
        app,
        [
            "verify-target-labels",
            "--path",
            str(relocated),
            "--manifest-sha256",
            verified["manifest_sha256"],
        ],
    )
    assert result.exit_code == 0 and json.loads(result.stdout)["verified"]


def test_label_export_keeps_features_and_database_counts_unchanged(label_package):
    engine, root, verified, output, _ = label_package
    from firewatch.pipeline.handoff import verify_handoff

    assert verify_handoff(root, verified["manifest_sha256"]) == verified
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 1
        assert session.scalar(select(func.count()).select_from(Detection)) > 0
    assert verify_label_package(output)["rows"] == 1


@pytest.mark.parametrize("change", ["label", "threshold", "missing_flag"])
def test_rehashed_label_tampering_fails_recomputation(label_package, change):
    _, _, _, root, _ = label_package
    path = root / "labels.jsonl"
    row = json.loads(path.read_bytes())
    if change == "label":
        row["label"], row["target_value"] = "positive", 1
    elif change == "threshold":
        row["threshold_mw"] = 123456
    else:
        row["unobserved_followup"] = True
    path.write_text(json.dumps(row) + "\n")
    manifest = json.loads((root / "manifest.json").read_bytes())
    manifest["files"][path.name] = dict(
        size=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest()
    )
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="recomputation"):
        verify_label_package(root)


def test_existing_output_and_wrong_handoff_hash_cannot_publish(label_package, tmp_path):
    engine, root, verified, output, _ = label_package
    args = dict(
        spec_path=Path("configs/target-v1.yaml"),
        design_path=Path("configs/target-dataset-v1.yaml"),
        outages_path=Path("configs/label-outages.yaml"),
    )
    with pytest.raises(ValueError, match="exists"):
        export_labels(engine, root, output, handoff_sha256=verified["manifest_sha256"], **args)
    missing = tmp_path / "wrong-hash"
    with pytest.raises(ValueError, match="checksum"):
        export_labels(engine, root, missing, handoff_sha256="0" * 64, **args)
    assert not missing.exists()


def test_cadence_plan_cli_is_offline():
    from typer.testing import CliRunner

    from firewatch.cli import app

    result = CliRunner().invoke(app, ["plan-target-dataset"])
    assert result.exit_code == 0 and json.loads(result.stdout)["total_candidate_cutoffs"] == 1472


def test_database_selected_followup_labels_positive_without_changing_features(sources, monkeypatch):
    import httpx
    from feature_factory import observation
    from label_factory import current

    from firewatch.ml.labels import label_snapshot

    def denied(*args, **kwargs):
        raise AssertionError("Provider HTTP is forbidden during offline labeling")

    monkeypatch.setattr(httpx.Client, "send", denied)
    engine, identifier = sources
    item = current(
        cutoff=T, rows=[observation(1, latitude=34.12345, longitude=71.12345, acquired=T, frp=10.0)]
    )
    item.region_id = identifier
    before = item.feature.model_dump_json()
    with Session(engine) as session, session.begin():
        session.execute(text("SET TRANSACTION READ ONLY"))
        evidence = select_followup(session, identifier, T, AVAILABLE)
    result = label_snapshot(item, evidence)
    assert result.label == "positive" and result.threshold_mw == 15
    assert result.later_detection_count == 1 and result.max_later_frp_mw == 100
    assert item.feature.model_dump_json() == before


def test_failed_label_export_cleans_its_staging(handoff, tmp_path, monkeypatch):  # noqa: F811
    import firewatch.ml.dataset as dataset

    engine, _, _, root, verified = handoff
    output = tmp_path / "failed-label-export"

    def denied(*args, **kwargs):
        raise OSError("synthetic disk error")

    monkeypatch.setattr(dataset, "_write", denied)
    with pytest.raises(OSError):
        export_labels(
            engine,
            root,
            output,
            handoff_sha256=verified["manifest_sha256"],
            spec_path=Path("configs/target-v1.yaml"),
            design_path=Path("configs/target-dataset-v1.yaml"),
            outages_path=Path("configs/label-outages.yaml"),
        )
    assert not output.exists() and not list(tmp_path.glob(".target-labels-*"))
    assert root.exists()
