"""Source-bound evaluation package verification against synthetic pipeline handoffs."""

import hashlib
import json
import shutil

import pytest
from test_forest_package import forest_package  # noqa: F401
from test_label_dataset import label_package  # noqa: F401
from test_pipeline_handoff import handoff  # noqa: F401
from test_replay import bundle_source  # noqa: F401
from test_split_package import rehash, split_package  # noqa: F401
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.evaluation_package import export_development, export_final, verify_evaluation


@pytest.fixture
def evaluation_package(forest_package, tmp_path):  # noqa: F811
    _, training, _, _ = forest_package
    sha = hashlib.sha256((training / "manifest.json").read_bytes()).hexdigest()
    root = tmp_path / "evaluation"
    report = export_development(training, root, sha)
    return training, root, sha, report


def test_evaluation_roundtrip_offline_cli_and_sealed_holdout(
    evaluation_package, tmp_path, monkeypatch
):
    import httpx
    from sqlalchemy.engine import Engine

    _, root, _, report = evaluation_package
    assert report["status"] == "insufficient_data"
    assert report["final_test_status"] == "sealed" and not report["promotion_performed"]
    assert json.loads((root / "evaluation-models.json").read_bytes()) is None

    def denied(*args, **kwargs):
        raise AssertionError("Offline evaluation accessed a provider or database")

    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(Engine, "connect", denied)
    verified = verify_evaluation(root)
    moved = tmp_path / "moved"
    shutil.copytree(root, moved)
    assert verify_evaluation(moved, verified["manifest_sha256"]) == verified
    cli = CliRunner().invoke(app, ["verify-evaluation", "--path", str(moved)])
    assert cli.exit_code == 0 and json.loads(cli.stdout)["verified"]
    ledger = tmp_path / "never-created.sqlite3"
    with pytest.raises(ValueError, match="Insufficient"):
        export_final(root, tmp_path / "final", verified["manifest_sha256"], ledger)
    assert not ledger.exists() and not (tmp_path / "final").exists()


@pytest.mark.parametrize(
    "change", ["claim", "model", "policy", "card", "dependencies", "traversal", "inventory"]
)
def test_rehashed_evaluation_tampering_is_rejected(evaluation_package, change):
    _, root, _, _ = evaluation_package
    if change in {"claim", "model", "policy", "card"}:
        name = {
            "claim": "evaluation-report.json",
            "model": "evaluation-models.json",
            "policy": "evaluation-spec.json",
            "card": "model-card.md",
        }[change]
        path = root / name
        if change == "card":
            path.write_text("Model achieved 99% accuracy")
        else:
            value = json.loads(path.read_bytes())
            if change == "claim":
                value["final_test_status"] = "opened_once"
            elif change == "policy":
                value["bootstrap_seed"] = 99
            else:
                value = {"__reduce__": "payload"}
            path.write_text(json.dumps(value))
        rehash(root, name)
    else:
        path = root / "manifest.json"
        value = json.loads(path.read_bytes())
        if change == "dependencies":
            value["dependencies"]["matplotlib"] = "untrusted"
        elif change == "traversal":
            value["files"]["../unsafe"] = dict(size=0, sha256="0" * 64)
        else:
            del value["files"]["evaluation-models.json"]
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        verify_evaluation(root)


def test_existing_output_and_failure_cleanup(evaluation_package, tmp_path, monkeypatch):
    import firewatch.ml.evaluation_package as package

    source, root, sha, _ = evaluation_package
    before = (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        export_development(source, root, sha)
    assert (root / "manifest.json").read_bytes() == before
    with pytest.raises(ValueError, match="checksum"):
        export_development(source, tmp_path / "bad", "0" * 64)

    def denied(*args, **kwargs):
        raise ValueError("synthetic write failure")

    monkeypatch.setattr(package, "write_model_card", denied)
    with pytest.raises(ValueError, match="synthetic"):
        export_development(source, tmp_path / "failed", sha)
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".release-evaluation-*"))
