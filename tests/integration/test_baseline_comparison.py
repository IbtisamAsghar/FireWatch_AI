"""PostgreSQL-backed fixtures; comparison and verification themselves stay offline."""

import hashlib
import json
import shutil

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_label_dataset import label_package  # noqa: F401
from test_pipeline_handoff import handoff  # noqa: F401
from test_replay import bundle_source  # noqa: F401
from test_split_package import rehash, split_package  # noqa: F401
from test_worker import CLOCK, work  # noqa: F401
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.db.models import Job, Run, Score
from firewatch.ml.comparison_package import export_comparison, verify_comparison
from firewatch.sources.firms_normalize import digest
from firewatch.worker import process_one


@pytest.fixture
def comparison(split_package, tmp_path):  # noqa: F811
    _, source, _ = split_package
    output = tmp_path / "comparison"
    sha = hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
    report = export_comparison(source, output, sha)
    return source, output, sha, report


def test_comparison_roundtrip_relocation_and_offline_cli(comparison, tmp_path, monkeypatch):
    import httpx
    from sqlalchemy.engine import Engine

    _, root, _, report = comparison
    assert report["status"] == "insufficient_data" and report["cohorts"] == []

    def denied(*args, **kwargs):
        raise AssertionError("Offline comparison contacted a database or provider")

    monkeypatch.setattr(httpx.Client, "send", denied)
    monkeypatch.setattr(Engine, "connect", denied)
    verified = verify_comparison(root)
    relocated = tmp_path / "relocated"
    shutil.copytree(root, relocated)
    assert verify_comparison(relocated, verified["manifest_sha256"]) == verified
    result = CliRunner().invoke(app, ["verify-baseline-comparison", "--path", str(relocated)])
    assert result.exit_code == 0 and json.loads(result.stdout)["verified"]
    with pytest.raises(ValueError, match="checksum"):
        verify_comparison(relocated, "0" * 64)


@pytest.mark.parametrize("change", ["metrics", "claim", "dependencies", "path", "missing"])
def test_comparison_rehashed_tampering_cannot_publish(comparison, change):
    _, root, _, _ = comparison
    if change in {"metrics", "claim"}:
        path = root / "comparison.json"
        report = json.loads(path.read_bytes())
        if change == "metrics":
            report["cohort_count"] = 1
        else:
            report["probability_supported"] = True
        path.write_text(json.dumps(report))
        rehash(root, path.name)
    else:
        path = root / "manifest.json"
        manifest = json.loads(path.read_bytes())
        if change == "dependencies":
            manifest["dependencies"]["numpy"] = "unreviewed"
        elif change == "path":
            manifest["files"]["../outside"] = dict(size=0, sha256="0" * 64)
        else:
            del manifest["files"]["comparison.json"]
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_comparison(root)


def test_comparison_existing_output_and_failed_staging_preserve_source(
    comparison, tmp_path, monkeypatch
):
    import firewatch.ml.comparison_package as module

    source, root, sha, _ = comparison
    original = (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        export_comparison(source, root, sha)
    assert original == (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="checksum"):
        export_comparison(source, tmp_path / "bad-hash", "0" * 64)

    def denied(*args):
        raise ValueError("simulated verification failure")

    monkeypatch.setattr(module, "verify_comparison", denied)
    with pytest.raises(ValueError, match="simulated"):
        export_comparison(source, tmp_path / "failed", sha)
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".baseline-comparison-*"))


def test_comparison_cli_build_reports_skipped_logistic(comparison, tmp_path):
    source, _, sha, _ = comparison
    result = CliRunner().invoke(
        app,
        [
            "compare-baselines",
            "--splits",
            str(source),
            "--manifest-sha256",
            sha,
            "--output",
            str(tmp_path / "cli"),
        ],
    )
    assert result.exit_code == 0
    assert json.loads(result.stdout)["logistic_status"] == "skipped_insufficient_data"


def test_weighted_worker_persists_baseline_score_without_artifact_or_probability(work, monkeypatch):  # noqa: F811
    import httpx

    engine, config, identifier, _ = work
    config.active_scorer = "weighted-baseline-v1"
    with Session(engine) as session, session.begin():
        job = session.get(Job, identifier)
        job.payload = job.payload | {"scorer_version": "weighted-baseline-v1"}
        job.payload_hash = digest(job.payload)

    def denied(*args, **kwargs):
        raise AssertionError("Offline replay attempted provider access")

    monkeypatch.setattr(httpx.Client, "send", denied)
    assert process_one(engine, config, now=lambda: CLOCK)
    with Session(engine) as session:
        job = session.get(Job, identifier)
        assert job.status == "succeeded"
        run = session.get(Run, job.result_run_id)
        assert run.scorer_version == "weighted-baseline-v1"
        scores = list(session.scalars(select(Score).where(Score.run_id == run.id)))
        assert scores and all(
            s.model_id is None and s.escalation_probability is None for s in scores
        )
        assert all(0 <= s.priority_score <= 100 for s in scores)
        assert scores[0].explanation["policy"]["freeze_basis"] == "prespecified_prototype"
