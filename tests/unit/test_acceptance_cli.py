"""Reject malformed trust anchors before touching the filesystem."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.acceptance import export_baseline_release


@pytest.mark.parametrize("sha", ["", "A" * 64, "0" * 63, "../manifest.json"])
def test_export_requires_external_trust_anchors(sha, tmp_path):
    with pytest.raises(ValueError, match="trusted manifest"):
        export_baseline_release(Path("absent"), Path("absent"), tmp_path / "out", sha, "a" * 64)
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("command", ["export-baseline-release", "verify-baseline-release"])
def test_acceptance_cli_help_needs_no_settings(command, monkeypatch):
    import firewatch.config

    def denied():
        raise AssertionError("CLI help accessed settings")

    monkeypatch.setattr(firewatch.config, "get_settings", denied)
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("change", ["final", "fitted"])
def test_ineligible_sources_refused_before_recursive_recomputation(tmp_path, monkeypatch, change):
    import hashlib
    import json

    import firewatch.ml.acceptance as acceptance

    source = tmp_path / "source"
    (source / "training").mkdir(parents=True)
    training = json.dumps({"model_available": change == "fitted"}).encode()
    (source / "training/manifest.json").write_bytes(training)
    manifest = {
        "stage": "final" if change == "final" else "development",
        "final_test_status": "opened_once" if change == "final" else "sealed",
        "files": {
            "training/manifest.json": {
                "size": len(training),
                "sha256": hashlib.sha256(training).hexdigest(),
            }
        },
    }
    raw = json.dumps(manifest).encode()
    (source / "manifest.json").write_bytes(raw)

    def denied(*args, **kwargs):
        raise AssertionError("Rejected source reached recomputation or final holdout")

    monkeypatch.setattr(acceptance, "verify_evaluation", denied)
    with pytest.raises(ValueError, match="sealed development|no fitted forest"):
        export_baseline_release(
            source,
            Path("absent"),
            tmp_path / "out",
            hashlib.sha256(raw).hexdigest(),
            "a" * 64,
        )
    assert not (tmp_path / "out").exists()
