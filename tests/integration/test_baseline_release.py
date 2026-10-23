"""Phase 4.8 source-bound baseline release, relocation and honest API handoff."""

import hashlib
import json
import shutil

import pytest
from test_evaluation_package import evaluation_package  # noqa: F401
from test_forest_package import forest_package  # noqa: F401
from test_label_dataset import label_package  # noqa: F401
from test_pipeline_handoff import handoff  # noqa: F401
from test_replay import bundle_source  # noqa: F401
from test_split_package import rehash, split_package  # noqa: F401
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.acceptance import export_baseline_release, verify_baseline_release
from firewatch.ml.comparison_package import export_comparison


@pytest.fixture
def sources(evaluation_package, tmp_path):  # noqa: F811
    training, evaluation, _, _ = evaluation_package
    split_sha = json.loads((training / "manifest.json").read_bytes())["split_manifest_sha256"]
    comparison = tmp_path / "comparison"
    export_comparison(training / "split", comparison, split_sha)
    return evaluation, comparison


def export(sources, output):
    evaluation, comparison = sources
    return export_baseline_release(
        evaluation,
        comparison,
        output,
        hashlib.sha256((evaluation / "manifest.json").read_bytes()).hexdigest(),
        hashlib.sha256((comparison / "manifest.json").read_bytes()).hexdigest(),
    )


def test_portable_offline_release_and_reproduction(sources, tmp_path, monkeypatch):
    import httpx
    import joblib
    from sqlalchemy.engine import Engine

    def denied(*args, **kwargs):
        raise AssertionError("Acceptance used provider/DB/pickle")

    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(Engine, "connect", denied)
    monkeypatch.setattr(joblib, "load", denied)
    root = tmp_path / "release"
    verified = export(sources, root)
    relocated = tmp_path / "moved"
    shutil.copytree(root, relocated)
    assert verify_baseline_release(relocated, verified["manifest_sha256"]) == verified
    report = json.loads((root / "release-report.json").read_bytes())
    assert (
        report["approved_bundle"] is report["held_out_ml_metrics"] is report["probability"] is None
    )
    assert not report["activation_performed"] and report["final_test_status"] == "sealed"
    assert (root / "evaluation/training/manifest.json").is_file()
    assert "baseline-v1" in (root / "model-card.md").read_text()
    assert "evaluate --final" not in (root / "REPRODUCE.md").read_text()
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "verify-baseline-release",
            "--path",
            str(relocated),
            "--manifest-sha256",
            verified["manifest_sha256"],
        ],
    )
    assert result.exit_code == 0, result.output
    # Reproduction must work outside the source checkout using only packaged policies.
    monkeypatch.chdir(relocated)
    training = tmp_path / "reproduced-training"
    split_sha = report["split_manifest_sha256"]
    trained = runner.invoke(
        app,
        [
            "train",
            "--splits",
            "evaluation/training/split",
            "--manifest-sha256",
            split_sha,
            "--spec",
            "evaluation/training/training-spec.json",
            "--output",
            str(training),
        ],
    )
    assert trained.exit_code == 0, trained.output
    training_sha = hashlib.sha256((training / "manifest.json").read_bytes()).hexdigest()
    evaluated = runner.invoke(
        app,
        [
            "evaluate",
            "--training",
            str(training),
            "--manifest-sha256",
            training_sha,
            "--spec",
            "evaluation/evaluation-spec.json",
            "--output",
            str(tmp_path / "reproduced-eval"),
        ],
    )
    assert evaluated.exit_code == 0, evaluated.output
    assert json.loads(evaluated.output)["status"] == "insufficient_data"
    assert not list(tmp_path.rglob("*.sqlite3"))


@pytest.mark.parametrize(
    "change",
    [
        "report",
        "card",
        "commands",
        "nested",
        "unexpected",
        "inventory",
        "traversal",
        "source_hash",
    ],
)
def test_tampering_even_with_rehashed_manifest_is_rejected(sources, tmp_path, change):
    root = tmp_path / "release"
    export(sources, root)
    names = {
        "report": "release-report.json",
        "card": "model-card.md",
        "commands": "REPRODUCE.md",
        "nested": "evaluation/training/model.json",
    }
    if change in names:
        name = names[change]
        (root / name).write_text("forged approval and 99% accuracy", encoding="utf-8")
        rehash(root, name)
    elif change == "unexpected":
        (root / ".env").write_text("private unbound file")
    else:
        manifest = json.loads((root / "manifest.json").read_bytes())
        if change == "inventory":
            del manifest["files"]["REPRODUCE.md"]
        elif change == "traversal":
            manifest["files"]["../outside"] = dict(size=0, sha256="0" * 64)
        else:
            manifest["evaluation_manifest_sha256"] = "0" * 64
        (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        verify_baseline_release(root)


def test_trusted_hash_output_safety_and_atomic_cleanup(sources, tmp_path, monkeypatch):
    import firewatch.ml.acceptance as acceptance

    root = tmp_path / "release"
    verified = export(sources, root)
    for sha in ("0" * 64, "", "../untrusted"):
        with pytest.raises(ValueError):
            verify_baseline_release(root, sha)
    with pytest.raises(ValueError, match="exists"):
        export(sources, root)
    with pytest.raises(ValueError, match="outside"):
        export(sources, sources[0] / "nested-output")
    assert verify_baseline_release(root, verified["manifest_sha256"]) == verified

    def failed(*args, **kwargs):
        raise ValueError("source mismatch")

    monkeypatch.setattr(acceptance, "_outputs", failed)
    with pytest.raises(ValueError, match="source mismatch"):
        export(sources, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".release-evaluation-*"))


def test_export_cli_and_safe_error(sources, tmp_path):
    evaluation, comparison = sources
    runner = CliRunner()
    args = [
        "export-baseline-release",
        "--evaluation",
        str(evaluation),
        "--evaluation-sha256",
        hashlib.sha256((evaluation / "manifest.json").read_bytes()).hexdigest(),
        "--comparison",
        str(comparison),
        "--comparison-sha256",
        hashlib.sha256((comparison / "manifest.json").read_bytes()).hexdigest(),
        "--output",
        str(tmp_path / "cli-release"),
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["approved_bundle"] is None
    result = runner.invoke(app, args)
    assert result.exit_code == 1 and "export failed" in result.output
    assert "Traceback" not in result.output
