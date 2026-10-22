"""Offline source-bound forest-package validation using synthetic DB-backed handoffs."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from test_label_dataset import label_package  # noqa: F401
from test_pipeline_handoff import handoff  # noqa: F401
from test_replay import bundle_source  # noqa: F401
from test_split_package import rehash, split_package  # noqa: F401
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.forest_package import export_forest, load_trusted_forest, verify_forest


@pytest.fixture
def forest_package(split_package, tmp_path):  # noqa: F811
    _, source, _ = split_package
    output = tmp_path / "forest"
    sha = hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
    report = export_forest(source, output, sha)
    return source, output, sha, report


def test_forest_roundtrip_relocation_cli_and_no_fake_fit(forest_package, tmp_path, monkeypatch):
    import httpx
    import joblib
    from sqlalchemy.engine import Engine

    source, root, _, report = forest_package
    assert report["status"] == "insufficient_data" and not report["probability_supported"]
    assert (root / "model.json").read_text().strip() == "null"

    def denied(*args, **kwargs):
        raise AssertionError("Offline forest verification used provider/DB/pickle loader")

    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(Engine, "connect", denied)
    monkeypatch.setattr(joblib, "load", denied)
    verified = verify_forest(root)
    relocated = tmp_path / "relocated"
    shutil.copytree(root, relocated)
    assert verify_forest(relocated, verified["manifest_sha256"]) == verified
    result = CliRunner().invoke(app, ["verify-forest", "--path", str(relocated)])
    assert result.exit_code == 0 and json.loads(result.stdout)["verified"]
    with pytest.raises(ValueError, match="No forest"):
        load_trusted_forest(relocated, verified["manifest_sha256"])
    with pytest.raises(ValueError, match="checksum"):
        verify_forest(root, "0" * 64)
    assert hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest() == verified_source(
        root
    )


def verified_source(root):
    return json.loads((root / "manifest.json").read_bytes())["split_manifest_sha256"]


@pytest.mark.parametrize(
    "change", ["claim", "model", "config", "dependencies", "traversal", "inventory"]
)
def test_rehashed_forest_tampering_cannot_load(forest_package, change):
    _, root, _, _ = forest_package
    if change in {"claim", "model", "config"}:
        name = {
            "claim": "training-report.json",
            "model": "model.json",
            "config": "training-spec.json",
        }[change]
        path = root / name
        value = json.loads(path.read_bytes())
        if change == "claim":
            value["probability_supported"] = True
        elif change == "model":
            value = {"__reduce__": "untrusted executable payload"}
        else:
            value["random_seed"] = 0
        path.write_text(json.dumps(value))
        rehash(root, name)
    else:
        path = root / "manifest.json"
        manifest = json.loads(path.read_bytes())
        if change == "dependencies":
            manifest["dependencies"]["scikit-learn"] = "unreviewed"
        elif change == "traversal":
            manifest["files"]["../unsafe.pkl"] = dict(size=0, sha256="0" * 64)
        else:
            del manifest["files"]["model.json"]
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_forest(root)


def test_forest_existing_output_bad_source_and_cleanup(forest_package, tmp_path, monkeypatch):
    import firewatch.ml.forest_package as module

    source, root, sha, _ = forest_package
    before = (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        export_forest(source, root, sha)
    assert before == (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="checksum"):
        export_forest(source, tmp_path / "bad", "0" * 64)
    with pytest.raises(ValueError, match="trusted"):
        load_trusted_forest(root, "")

    def denied(*args, **kwargs):
        raise ValueError("simulated verification failure")

    monkeypatch.setattr(module, "verify_forest", denied)
    with pytest.raises(ValueError, match="simulated"):
        export_forest(source, tmp_path / "failed", sha)
    assert not (tmp_path / "failed").exists() and not list(tmp_path.glob(".forest-training-*"))


def test_training_cli_is_offline_and_reports_honest_data_gate(forest_package, tmp_path):
    source, _, sha, _ = forest_package
    result = CliRunner().invoke(
        app,
        [
            "train-forest",
            "--splits",
            str(source),
            "--manifest-sha256",
            sha,
            "--output",
            str(tmp_path / "cli-forest"),
            "--spec",
            str(Path("configs/forest-v1.yaml")),
        ],
    )
    assert result.exit_code == 0 and json.loads(result.stdout)["status"] == "insufficient_data"
