"""Parquet/status handoff tests use synthetic provider-shaped replay inputs only."""

import hashlib
import json
import shutil
from uuid import uuid4

import pyarrow.parquet as pq
import pytest
from conftest import isolated_schema, migrate
from test_replay import CLOCK, bundle_source  # noqa: F401

from firewatch.pipeline.handoff import export_handoff, verify_handoff
from firewatch.pipeline.replay import replay_scenario
from firewatch.processing.features import MODEL_INPUT_NAMES

pytestmark = pytest.mark.integration


@pytest.fixture
def handoff(bundle_source, tmp_path):  # noqa: F811 - imported pytest fixture
    root, _, settings = bundle_source
    with isolated_schema() as engine:
        migrate(engine)
        run = replay_scenario(engine, settings, root.name, now=lambda: CLOCK)
        from uuid import UUID

        run_id = UUID(run["run_id"])
        output = tmp_path / "handoff"
        report = export_handoff(
            engine, settings, [run_id], output, data_kind="synthetic", now=CLOCK
        )
        yield engine, settings, run_id, output, report


def rehash(root, name):
    manifest = json.loads((root / "manifest.json").read_bytes())
    content = (root / name).read_bytes()
    manifest["files"][name] = {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    (root / "manifest.json").write_text(json.dumps(manifest))


def test_export_roundtrip_and_relocation_preserve_features_status_and_joins(handoff, tmp_path):
    _, _, _, root, report = handoff
    assert report["verified"] and report["features"] == 1 and report["memberships"] == 1
    schema = json.loads((root / "schema.json").read_bytes())
    assert schema["model_input_names"] == list(MODEL_INPUT_NAMES)
    assert all(
        name not in MODEL_INPUT_NAMES for name in ("latitude", "label", "priority_score", "run_id")
    )
    row = pq.read_table(root / "features.parquet").to_pylist()[0]
    assert row["temperature_c"] == 10 and row["temperature_c_missing"] is False
    assert row["as_of"].tzinfo and row["latest_detection_at"] <= row["as_of"] < row["processed_at"]
    assert all(r["status"] == "completed" for r in json.loads((root / "runs.json").read_bytes()))
    assert json.loads((root / "manifest.json").read_bytes())["data_kind"] == "synthetic"
    relocated = tmp_path / "relocated"
    shutil.copytree(root, relocated)
    assert verify_handoff(relocated, report["manifest_sha256"]) == report


@pytest.mark.parametrize("change", ["future", "orphan", "duplicate"])
def test_semantic_membership_tampering_is_rejected_even_with_updated_file_hash(handoff, change):
    _, _, _, root, _ = handoff
    file = root / "memberships.parquet"
    table = pq.read_table(file)
    rows = table.to_pylist()
    if change == "future":
        from datetime import timedelta

        rows[0]["acquired_at"] += timedelta(days=2)
    elif change == "orphan":
        rows[0]["detection_id"] = str(uuid4())
    else:
        rows.append(dict(rows[0]))
    import pyarrow as pa

    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), file)
    rehash(root, file.name)
    with pytest.raises(ValueError, match="member/source/time"):
        verify_handoff(root)


@pytest.mark.parametrize("change", ["value", "missing"])
def test_feature_integrity_and_missing_flags_survive_parquet_readback(handoff, change):
    _, _, _, root, _ = handoff
    file = root / "features.parquet"
    table = pq.read_table(file)
    rows = table.to_pylist()
    if change == "value":
        rows[0]["temperature_c"] = 99
    else:
        rows[0]["temperature_c_missing"] = True
    import pyarrow as pa

    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), file)
    rehash(root, file.name)
    with pytest.raises(ValueError):
        verify_handoff(root)


def test_file_hash_and_trusted_manifest_binding_are_checked(handoff):
    _, _, _, root, report = handoff
    with pytest.raises(ValueError, match="manifest checksum"):
        verify_handoff(root, "0" * 64)
    with (root / "features.parquet").open("ab") as file:
        file.write(b"tampered")
    with pytest.raises(ValueError, match="file checksum"):
        verify_handoff(root, report["manifest_sha256"])


def test_feature_whitelist_cannot_be_changed_by_manifest_rehash(handoff):
    _, _, _, root, _ = handoff
    path = root / "schema.json"
    body = json.loads(path.read_bytes())
    body["model_input_names"].append("latitude")
    path.write_text(json.dumps(body))
    rehash(root, path.name)
    with pytest.raises(ValueError, match="whitelist"):
        verify_handoff(root)


def test_export_never_overwrites_or_publishes_partial_artifacts(handoff, tmp_path, monkeypatch):
    engine, settings, run_id, root, _ = handoff
    before = (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        export_handoff(engine, settings, [run_id], root, data_kind="synthetic")
    assert (root / "manifest.json").read_bytes() == before

    def fail(*args, **kwargs):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(pq, "write_table", fail)
    output = tmp_path / "failed"
    with pytest.raises(OSError):
        export_handoff(engine, settings, [run_id], output, data_kind="synthetic")
    assert not output.exists() and not list(tmp_path.glob(".pipeline-handoff-*"))


def test_unknown_and_duplicate_run_selection_are_rejected(handoff, tmp_path):
    engine, settings, run_id, _, _ = handoff
    with pytest.raises(ValueError, match="unique"):
        export_handoff(engine, settings, [run_id, run_id], tmp_path / "duplicates")
    with pytest.raises(ValueError, match="completed"):
        export_handoff(engine, settings, [uuid4()], tmp_path / "unknown")
    assert not (tmp_path / "unknown").exists()


def test_empty_completed_run_exports_typed_empty_parquet(empty_engine, tmp_path):
    from conftest import migrate
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from test_snapshot_persistence import setup_sources
    from test_worker import CLOCK, queued, settings

    from firewatch.db.models import IngestionBatch, Job
    from firewatch.pipeline.handoff import feature_schema
    from firewatch.worker import process_one

    migrate(empty_engine)
    with Session(empty_engine) as session, session.begin():
        area, _, policy = setup_sources(session, tmp_path, empty=True)
        area.enabled = True
        assets = list(
            session.scalars(
                select(IngestionBatch.asset_id).where(IngestionBatch.id.in_(policy.batch_ids))
            )
        )
        identifier = queued(session, area.id, assets=assets)
    config = settings(tmp_path)
    assert process_one(empty_engine, config, now=lambda: CLOCK)
    with Session(empty_engine) as session:
        run_id = session.get(Job, identifier).result_run_id
    root = tmp_path / "empty-handoff"
    result = export_handoff(empty_engine, config, [run_id], root, data_kind="synthetic")
    assert result["features"] == result["memberships"] == 0 and result["runs"] == 1
    assert pq.read_table(root / "features.parquet").num_rows == 0
    assert pq.read_schema(root / "features.parquet").equals(feature_schema(), check_metadata=True)


def test_verification_cli_supports_trusted_hash_and_rejects_tampering(handoff):
    from typer.testing import CliRunner

    from firewatch.cli import app

    _, _, _, root, report = handoff
    command = [
        "verify-pipeline-handoff",
        "--path",
        str(root),
        "--manifest-sha256",
        report["manifest_sha256"],
    ]
    result = CliRunner().invoke(app, command)
    assert result.exit_code == 0 and json.loads(result.stdout)["verified"]
    (root / "schema.json").write_bytes(b"tampered")
    failed = CliRunner().invoke(app, command)
    assert failed.exit_code == 1 and "verification failed" in failed.output


def test_provenance_is_required_even_when_payload_hashes_are_regenerated(handoff):
    _, _, _, root, _ = handoff
    (root / "provenance.json").write_text("[]")
    rehash(root, "provenance.json")
    with pytest.raises(ValueError, match="every run"):
        verify_handoff(root)
