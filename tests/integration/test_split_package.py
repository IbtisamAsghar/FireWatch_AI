"""Portable split integrity tests; catalog fixtures are synthetic partition metadata."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from split_factory import cohort
from test_label_dataset import label_package  # noqa: F401
from test_pipeline_handoff import handoff  # noqa: F401
from test_replay import bundle_source  # noqa: F401
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.split_package import export_splits, verify_split_package


@pytest.fixture
def split_package(label_package, tmp_path, monkeypatch):  # noqa: F811
    import firewatch.ml.split_package as module

    _, _, _, labels, _ = label_package
    _, catalog, _ = cohort()
    monkeypatch.setattr(module, "load_source_observations", lambda *args: catalog.observations)
    output = tmp_path / "splits"
    args = dict(
        label_sha256=hashlib.sha256((labels / "manifest.json").read_bytes()).hexdigest(),
        sources=tmp_path / "synthetic-source",
        source_sha256=catalog.source_manifest_sha256,
        spec_path=Path("configs/splits-v1.yaml"),
    )
    export_splits(labels, output, **args)
    return labels, output, args


def rehash(root, name):
    manifest = json.loads((root / "manifest.json").read_bytes())
    raw = (root / name).read_bytes()
    manifest["files"][name] = dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / "manifest.json").write_text(json.dumps(manifest))


def test_split_roundtrip_relocation_cli_and_sealed_fallback(split_package, tmp_path):
    _, root, _ = split_package
    result = verify_split_package(root)
    assert result["strategy"] == "baseline_only" and not result["training_ready"]
    assert result["final_test_status"] == "sealed"
    moved = tmp_path / "relocated"
    shutil.copytree(root, moved)
    assert verify_split_package(moved, result["manifest_sha256"]) == result
    command = CliRunner().invoke(app, ["verify-splits", "--path", str(moved)])
    assert command.exit_code == 0 and json.loads(command.stdout)["verified"]
    with pytest.raises(ValueError, match="checksum"):
        verify_split_package(moved, "0" * 64)


@pytest.mark.parametrize("name", ["membership.json", "event-catalog.json", "inspection.json"])
def test_rehashed_split_tampering_is_rejected(split_package, name):
    _, root, _ = split_package
    path = root / name
    value = json.loads(path.read_bytes())
    if name == "membership.json":
        value["training_ready"] = True
    elif name == "event-catalog.json":
        value["groups"][0]["physical_keys"] = []
    else:
        value["observations"] += 1
    path.write_text(json.dumps(value))
    rehash(root, name)
    with pytest.raises(ValueError):
        verify_split_package(root)


@pytest.mark.parametrize("change", ["missing", "traversal", "unhashed"])
def test_split_inventory_and_paths_are_checked(split_package, change):
    _, root, _ = split_package
    path = root / "manifest.json"
    manifest = json.loads(path.read_bytes())
    if change == "missing":
        del manifest["files"]["event-review.json"]
    elif change == "traversal":
        manifest["files"]["../outside.json"] = dict(size=0, sha256="0" * 64)
    else:
        (root / "membership.json").write_text("{}")
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_split_package(root)


def test_existing_split_output_preserved_and_failed_staging_removed(
    split_package, tmp_path, monkeypatch
):
    import firewatch.ml.split_package as module

    labels, root, args = split_package
    before = (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        export_splits(labels, root, **args)
    assert (root / "manifest.json").read_bytes() == before

    def denied(*args):
        raise ValueError("simulated final verification failure")

    monkeypatch.setattr(module, "verify_split_package", denied)
    with pytest.raises(ValueError, match="simulated"):
        export_splits(labels, tmp_path / "failed", **args)
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".event-splits-*"))
