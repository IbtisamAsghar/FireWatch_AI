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


@pytest.mark.parametrize(
    "change",
    [
        "extra_feature_hash",
        "snapshot_binding",
        "provenance_region",
        "duplicate_snapshot",
        "duplicate_revision",
        "duplicate_job",
        "duplicate_run_id",
    ],
)
def test_cross_stage_handoff_inconsistency_is_rejected(handoff, change):
    from firewatch.sources.firms_normalize import digest

    _, _, _, root, _ = handoff
    if change in {"duplicate_job", "duplicate_run_id"}:
        manifest = json.loads((root / "manifest.json").read_bytes())
        if change == "duplicate_run_id":
            manifest["run_ids"].append(manifest["run_ids"][0])
        else:
            jobs = json.loads((root / "jobs.json").read_bytes())
            jobs.append(dict(jobs[0]))
            (root / "jobs.json").write_text(json.dumps(jobs))
            manifest["row_counts"]["jobs"] += 1
        (root / "manifest.json").write_text(json.dumps(manifest))
        if change == "duplicate_job":
            rehash(root, "jobs.json")
    else:
        path = root / "provenance.json"
        rows = json.loads(path.read_bytes())
        entry = rows[0]
        stages = entry["stage_manifests"]
        features = stages["features"]
        if change == "extra_feature_hash":
            features["feature_hashes"][str(uuid4())] = "0" * 64
        elif change == "snapshot_binding":
            features["snapshot_sha256"] = "0" * 64
        elif change == "provenance_region":
            entry["region_id"] = str(uuid4())
        elif change == "duplicate_snapshot":
            stages["clustering"]["batch"]["snapshots"].append(
                stages["clustering"]["batch"]["snapshots"][0]
            )
            stages["clustering"]["sha256"] = digest(stages["clustering"]["batch"])
        else:
            stages["firms"]["selected"].append(stages["firms"]["selected"][0])
        features["sha256"] = digest({k: v for k, v in features.items() if k != "sha256"})
        path.write_text(json.dumps(rows))
        rehash(root, path.name)
    with pytest.raises(ValueError):
        verify_handoff(root)


def test_input_assessment_reports_eligibility_without_claiming_training_readiness(handoff):
    from typer.testing import CliRunner

    from firewatch.cli import app
    from firewatch.pipeline.handoff import assess_handoff

    _, _, _, root, verified = handoff
    report = assess_handoff(root, verified["manifest_sha256"])
    assert report["verified"] and not report["training_ready"] and not report["labels_included"]
    assert report["feature_rows"] == report["target_input_eligible_rows"] == 1
    assert report["excluded_rows"] == report["observations_reused_across_snapshots"] == 0
    command = [
        "assess-pipeline-handoff",
        "--path",
        str(root),
        "--manifest-sha256",
        verified["manifest_sha256"],
    ]
    result = CliRunner().invoke(app, command)
    assert result.exit_code == 0 and json.loads(result.stdout) == report
    failed = CliRunner().invoke(
        app, ["assess-pipeline-handoff", "--path", str(root), "--manifest-sha256", "0" * 64]
    )
    assert failed.exit_code == 1


def test_feature_lineage_must_match_source_pins_even_after_rehash(handoff):
    import pyarrow as pa

    from firewatch.processing.features import FEATURE_NAMES, MISSING_NAMES, UNITS
    from firewatch.schemas.contracts import FeatureSet
    from firewatch.sources.firms_normalize import digest

    _, _, _, root, _ = handoff
    path = root / "features.parquet"
    table = pq.read_table(path)
    rows = table.to_pylist()
    row = rows[0]
    lineage = json.loads(row["lineage_json"])
    lineage["member_revisions"][0]["batch_id"] = str(uuid4())
    feature = FeatureSet(
        run_id=row["run_id"],
        snapshot_id=row["snapshot_id"],
        mode=row["mode"],
        as_of=row["as_of"],
        feature_version=row["feature_version"],
        values={n: row[n] for n in FEATURE_NAMES},
        missing_flags={n: row[n + "_missing"] for n in MISSING_NAMES},
        units=UNITS,
        lineage=lineage,
        feature_hash=row["feature_hash"],
    )
    row["feature_hash"] = digest(feature.model_dump(mode="json", exclude={"feature_hash"}))
    row["lineage_json"] = json.dumps(lineage)
    pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), path)
    rehash(root, path.name)
    path = root / "provenance.json"
    entries = json.loads(path.read_bytes())
    stage = entries[0]["stage_manifests"]["features"]
    stage["feature_hashes"][row["snapshot_id"]] = row["feature_hash"]
    stage["sha256"] = digest({k: v for k, v in stage.items() if k != "sha256"})
    path.write_text(json.dumps(entries))
    rehash(root, path.name)
    with pytest.raises(ValueError, match="lineage"):
        verify_handoff(root)
