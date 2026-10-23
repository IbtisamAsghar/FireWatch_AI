"""Synthetic release-boundary checks; mocked source authority is never real ML evidence."""

import hashlib
import json

import pytest
from forest_factory import small_spec
from inference_factory import candidate
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.evaluation_models import fit_evaluation_models
from firewatch.ml.inference_bundle import _write_bundle
from firewatch.ml.registry_artifacts import (
    managed_path,
    prepare_promotion,
    verify_managed_release,
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def manifest(root, **metadata):
    value = metadata | {
        "files": {
            p.relative_to(root).as_posix(): dict(
                size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
            )
            for p in root.rglob("*")
            if p.is_file() and p != root / "manifest.json"
        }
    }
    write(root / "manifest.json", value)
    return hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def fitted_promotion():
    data, model, thresholds = candidate()
    models = fit_evaluation_models(data, model, small_spec())
    return model, thresholds, models


@pytest.fixture
def release(tmp_path, fitted_promotion, monkeypatch):
    import firewatch.ml.evaluation_package as evaluation_package

    model, thresholds, models = fitted_promotion
    source = tmp_path / "evaluation"
    report = dict(
        promotion_recommendation="independent_review_candidate",
        final_test_evaluated=True,
        final_test_status="opened_once",
        thresholds={"forest": thresholds.model_dump(mode="json")},
        final=dict(
            methods={
                "forest": {
                    "metrics": dict(events=6, positive=18, negative=18, average_precision=0.9)
                },
                "baseline-v1": {"metrics": {"average_precision": 0.4}},
                "weighted-baseline-v1": {"metrics": {"average_precision": 0.5}},
            },
            uncertainty=dict(
                status="estimated",
                repetitions=20,
                resampling_unit="whole_partition_event",
                paired_deltas={
                    "forest_minus_" + n: dict(lower=0.1, upper=0.3, valid_replicates=20)
                    for n in ("baseline-v1", "weighted-baseline-v1")
                },
            ),
        ),
    )
    write(source / "evaluation-report.json", report)
    write(source / "prepared/evaluation-models.json", models.model_dump(mode="json"))
    (source / "model-card.md").write_text("Synthetic test card; no operational performance claim.")
    sha = manifest(source)
    bundle = tmp_path / "bundle"
    bundle_sha = _write_bundle(bundle, model, thresholds, "synthetic-forest-v1", sha)
    review = dict(
        schema_version="model-approval-review-v1",
        decision="approve",
        reviewer="synthetic-reviewer",
        model_author="synthetic-author",
        independent_review=True,
        limitations_acknowledged=True,
        evaluation_manifest_sha256=sha,
        inference_manifest_sha256=bundle_sha,
        model_card_sha256=hashlib.sha256((source / "model-card.md").read_bytes()).hexdigest(),
        rationale="Synthetic test only: exercising explicit review and digest binding.",
        supported_modes=["replay"],
    )
    review_file = tmp_path / "review.json"
    write(review_file, review)
    monkeypatch.setattr(evaluation_package, "verify_evaluation", lambda *args: {"stage": "final"})
    return tmp_path / "models", source, sha, bundle, bundle_sha, review_file


def prepare(args):
    root, source, sha, bundle, bundle_sha, review = args
    return prepare_promotion(root, "synthetic-forest-v1", source, sha, bundle, bundle_sha, review)


def test_managed_copy_retains_bound_source_card_review_and_is_idempotent(release):
    first = prepare(release)
    verified = verify_managed_release(first.release_root, first.release_sha256)
    assert verified["evaluation_sha256"] == first.evaluation_sha256
    assert (first.release_root / "evaluation/model-card.md").exists()
    assert (first.release_root / "review.json").exists()
    assert prepare(release).release_sha256 == first.release_sha256
    assert first.review.independent_review and first.probability_supported


@pytest.mark.parametrize(
    "change",
    [
        "rejected",
        "no_final",
        "few_events",
        "few_classes",
        "ci_zero",
        "insufficient_replicates",
        "worse_ap",
        "wrong_card",
        "wrong_eval",
        "wrong_bundle",
        "self_review",
        "pending",
        "no_limits",
        "no_independence",
        "live_without_review",
        "wrong_source",
        "different_model",
    ],
)
def test_promotion_rejects_inadequate_or_mismatched_evidence(release, change, fitted_promotion):
    _, source, _, bundle, _, review_path = release
    report = json.loads((source / "evaluation-report.json").read_bytes())
    review = json.loads(review_path.read_bytes())
    if change == "rejected":
        report["promotion_recommendation"] = "baseline_only"
    elif change == "no_final":
        report["final_test_evaluated"] = False
    elif change == "few_events":
        report["final"]["methods"]["forest"]["metrics"]["events"] = 4
    elif change == "few_classes":
        report["final"]["methods"]["forest"]["metrics"]["positive"] = 9
    elif change == "worse_ap":
        report["final"]["methods"]["forest"]["metrics"]["average_precision"] = 0.3
    elif change in {"ci_zero", "insufficient_replicates"}:
        item = report["final"]["uncertainty"]["paired_deltas"]["forest_minus_baseline-v1"]
        item["lower" if change == "ci_zero" else "valid_replicates"] = (
            0 if change == "ci_zero" else 17
        )
    elif change in {"wrong_card", "wrong_eval", "wrong_bundle"}:
        key = {
            "wrong_card": "model_card_sha256",
            "wrong_eval": "evaluation_manifest_sha256",
            "wrong_bundle": "inference_manifest_sha256",
        }[change]
        review[key] = "0" * 64
    elif change == "self_review":
        review["reviewer"] = review["model_author"]
    elif change == "pending":
        review["decision"] = "pending"
    elif change == "no_limits":
        review["limitations_acknowledged"] = False
    elif change == "no_independence":
        review["independent_review"] = False
    elif change == "live_without_review":
        review["supported_modes"] = ["live"]
    elif change == "wrong_source":
        data = json.loads((bundle / "manifest.json").read_bytes())
        data["source_evaluation_sha256"] = "0" * 64
        write(bundle / "manifest.json", data)
    else:
        model_file = source / "prepared/evaluation-models.json"
        data = json.loads(model_file.read_bytes())
        data["forest"]["trees"][0]["nodes"][0]["impurity"] += 0.01
        write(model_file, data)
    write(source / "evaluation-report.json", report)
    write(review_path, review)
    with pytest.raises(ValueError):
        prepare(release)
    assert not (release[0] / "releases").exists()


def test_sealed_development_can_never_be_promoted(release, monkeypatch):
    import firewatch.ml.evaluation_package as evaluation_package

    monkeypatch.setattr(
        evaluation_package, "verify_evaluation", lambda *args: {"stage": "development"}
    )
    with pytest.raises(ValueError, match="final-test"):
        prepare(release)


@pytest.mark.parametrize("name", ["../escape", "/absolute"])
def test_managed_paths_cannot_escape_root(tmp_path, name):
    with pytest.raises(ValueError):
        managed_path(tmp_path, name)


def test_corruption_and_copy_failure_preserve_release_and_cleanup(release, monkeypatch):
    first = prepare(release)
    card = first.release_root / "evaluation/model-card.md"
    card.write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        verify_managed_release(first.release_root, first.release_sha256)
    with pytest.raises(ValueError):
        prepare(release)
    assert card.read_text() == "tampered"
    assert not list(first.release_root.parent.glob(".registry-stage-*"))


def test_train_evaluate_promotion_cli_help_needs_no_database(monkeypatch):
    import firewatch.cli as cli

    monkeypatch.setattr(cli, "get_settings", lambda: pytest.fail("help requested configuration"))
    for name in ("train", "evaluate", "promote-model", "rollback-model"):
        assert CliRunner().invoke(app, [name, "--help"]).exit_code == 0
    result = CliRunner().invoke(
        app, ["evaluate", "--manifest-sha256", "a" * 64, "--output", "unused"]
    )
    assert result.exit_code == 1


@pytest.mark.parametrize("operation", ["train", "development", "final"])
def test_generic_train_and_evaluate_commands_keep_training_offline(monkeypatch, operation):
    import firewatch.cli as cli
    import firewatch.ml.evaluation_package as evaluation
    import firewatch.ml.forest_package as forest

    monkeypatch.setattr(cli, "Database", lambda *args: pytest.fail("Offline command used database"))
    calls = []

    def record_train(*args):
        calls.append("train")
        return dict(
            status="insufficient_data",
            probability_supported=False,
            calibration={"status": "insufficient_data"},
            final_test_status="sealed",
            release_recommendation="baseline_only",
        )

    def record_development(*args):
        calls.append("development")
        return dict(
            status="insufficient_data",
            final_test_status="sealed",
            release_recommendation="baseline_only",
        )

    def record_final(*args):
        calls.append("final")
        return dict(final_test_status="opened_once", release_recommendation="baseline_only")

    monkeypatch.setattr(forest, "export_forest", record_train)
    monkeypatch.setattr(evaluation, "export_development", record_development)
    monkeypatch.setattr(evaluation, "export_final", record_final)
    argv = (
        ["train", "--splits", "source"]
        if operation == "train"
        else ["evaluate", "--training", "source"]
        if operation == "development"
        else ["evaluate", "--final", "--prepared", "source"]
    )
    result = CliRunner().invoke(app, argv + ["--manifest-sha256", "a" * 64, "--output", "unused"])
    assert result.exit_code == 0, result.stdout
    assert calls == [operation]
