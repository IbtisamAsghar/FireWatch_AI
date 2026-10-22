"""Verified source export refuses the actual unsupported/no-fit evaluation state."""

import hashlib

import pytest
from test_evaluation_package import evaluation_package  # noqa: F401
from test_forest_package import forest_package  # noqa: F401
from test_label_dataset import label_package  # noqa: F401
from test_pipeline_handoff import handoff  # noqa: F401
from test_replay import bundle_source  # noqa: F401
from test_split_package import split_package  # noqa: F401
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.inference_bundle import export_inference_bundle


def test_source_bound_export_cannot_invent_model_or_approve_candidate(
    evaluation_package,  # noqa: F811
    tmp_path,
):
    _, root, _, _ = evaluation_package
    sha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    output = tmp_path / "inference"
    with pytest.raises(ValueError, match="No eligible"):
        export_inference_bundle(root, output, sha, "forest-candidate-v1")
    assert not output.exists()
    assert not list(tmp_path.glob(".inference-bundle-*"))
    result = CliRunner().invoke(
        app,
        [
            "build-inference-bundle",
            "--evaluation",
            str(root),
            "--manifest-sha256",
            sha,
            "--scorer-version",
            "forest-candidate-v1",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 1 and not output.exists()
    with pytest.raises(ValueError, match="checksum"):
        export_inference_bundle(root, output, "0" * 64, "forest-candidate-v1")
