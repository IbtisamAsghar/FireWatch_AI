"""Synthetic inference fixtures; approval references here are test authority stubs."""

from uuid import uuid4

from forest_factory import forest_data, small_spec

from firewatch.ml.evaluation_spec import EvaluationSpec
from firewatch.ml.forest_training import train_forest
from firewatch.ml.inference_bundle import ApprovedScorer, _write_bundle
from firewatch.ml.priority_thresholds import PriorityThresholds
from firewatch.ml.release_evaluation import prepare_report
from firewatch.sources.firms_normalize import digest


def candidate():
    data, config = forest_data(calibration="accepted"), small_spec()
    result = train_forest(data, config)
    report, models = prepare_report(
        data, result.model, config, EvaluationSpec(bootstrap_repetitions=20)
    )
    thresholds = PriorityThresholds.model_validate(report["thresholds"]["forest"])
    return data, models.forest, thresholds


def bundle(root, model, thresholds, *, version="synthetic-forest-v1"):
    sha = _write_bundle(root, model, thresholds, version, digest("synthetic-evaluation-only"))
    return ApprovedScorer(
        model_id=uuid4(),
        scorer_version=version,
        artifact_path=root,
        manifest_sha256=sha,
        approval_status="approved",
        approval_reference="synthetic-test-authority-not-production",
    )
