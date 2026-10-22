"""Synthetic batch inference, authentic Shapley attribution and bundle trust tests."""

import hashlib
import itertools
import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import numpy as np
import pytest
from inference_factory import bundle, candidate
from sklearn.tree import DecisionTreeClassifier
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.explain import forest_explanation
from firewatch.ml.forest_artifact import ForestNode, ForestTree, transformed_inputs
from firewatch.ml.inference_bundle import ApprovedScorer, load_bundle
from firewatch.ml.predict import score_features
from firewatch.ml.tree_shap import PathDependentSHAP
from firewatch.processing.features import MODEL_INPUT_NAMES
from firewatch.schemas.contracts import ScoreResult
from firewatch.sources.firms_normalize import digest


@pytest.fixture(scope="module")
def fitted():
    return candidate()


@pytest.fixture(autouse=True)
def isolated_cache():
    import firewatch.ml.predict as scoring

    scoring._MODEL_CACHE.clear()
    yield
    scoring._MODEL_CACHE.clear()


@pytest.fixture
def approved(fitted, tmp_path):
    _, model, thresholds = fitted
    return bundle(tmp_path / "bundle", model, thresholds)


def weighted_conditional(tree, vector, known, index=0):
    node = tree.nodes[index]
    if node.left == -1:
        return node.class_values[1]
    if node.feature in known:
        return weighted_conditional(
            tree, vector, known, node.left if vector[node.feature] <= node.threshold else node.right
        )
    return sum(
        tree.nodes[child].weighted_samples
        / node.weighted_samples
        * weighted_conditional(tree, vector, known, child)
        for child in (node.left, node.right)
    )


@pytest.mark.parametrize("seed", range(6))
def test_exact_shap_matches_exhaustive_coalitions_with_repeated_splits(fitted, seed):
    _, template, _ = fitted
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(100, 4)).astype(np.float32)
    y = ((x[:, 0] * x[:, 1] + x[:, 2]) > 0).astype(int)
    estimator = DecisionTreeClassifier(max_depth=4, random_state=seed, class_weight="balanced").fit(
        x, y
    )
    source = estimator.tree_
    tree = ForestTree(
        nodes=[
            ForestNode(
                left=int(source.children_left[i]),
                right=int(source.children_right[i]),
                feature=int(source.feature[i]),
                threshold=float(source.threshold[i]),
                class_values=tuple(float(v) for v in source.value[i, 0] / source.value[i, 0].sum()),
                impurity=max(0.0, float(source.impurity[i])),
                samples=int(source.n_node_samples[i]),
                weighted_samples=float(source.weighted_n_node_samples[i]),
            )
            for i in range(source.node_count)
        ]
    )
    model = template.model_copy(deep=True, update={"trees": [tree] * 8})
    explainer = PathDependentSHAP(model)
    used = sorted({n.feature for n in tree.nodes if n.feature >= 0})
    for sample in x[:5]:
        vector = np.zeros(31, dtype=np.float32)
        vector[:4] = sample
        raw = weighted_conditional(tree, vector, set(used))
        actual = explainer.explain(vector, raw)
        expected = np.zeros(31)
        for feature in used:
            rest = [f for f in used if f != feature]
            for size in range(len(rest) + 1):
                weight = (
                    math.factorial(size)
                    * math.factorial(len(used) - size - 1)
                    / math.factorial(len(used))
                )
                for subset in itertools.combinations(rest, size):
                    expected[feature] += weight * (
                        weighted_conditional(tree, vector, set(subset) | {feature})
                        - weighted_conditional(tree, vector, set(subset))
                    )
        np.testing.assert_allclose(actual, expected, atol=1e-10, rtol=1e-9)
        assert explainer.base_value == pytest.approx(weighted_conditional(tree, vector, set()))
        assert raw == pytest.approx(estimator.predict_proba(sample.reshape(1, -1))[0, 1])


def test_batch_inference_matches_raw_and_calibrated_reference(fitted, approved):
    data, model, _ = fitted
    features = [r.target.feature for r in data.samples[:6]]
    actual = score_features(features, approved.scorer_version, approved=approved)
    reference = model.predict(features)
    assert [r.snapshot_id for r in actual] == [r.snapshot_id for r in features]
    assert len({r.scored_at for r in actual}) == 1
    for result, expected in zip(actual, reference, strict=True):
        assert result.priority_score == expected.escalation_probability
        assert result.score_kind == "probability" and result.model_id == approved.model_id
        exp = result.explanation
        assert exp["base_value"] + sum(
            v["contribution"] for v in exp["contributions"]
        ) == pytest.approx(expected.model_score)
        assert exp["raw_model_score"] == expected.model_score
        assert exp["additivity_target"] == "raw_model_score"
        assert "do not sum to calibrated probability" in exp["calibration_note"]
        assert exp["contribution_units"] == "raw_positive_class_forest_vote_fraction"
        assert result.warnings == result.quality_flags == exp["warnings"]
        assert len(result.reasons) <= 3
        assert ScoreResult.model_validate_json(result.model_dump_json()) == result


def test_signed_reasons_and_missing_imputation_never_claim_observed_weather(fitted):
    data, model, _ = fitted
    row = data.samples[0].target.feature.model_copy(deep=True)
    row.values["wind_ms"] = None
    row.missing_flags["wind_ms"] = True
    row.feature_hash = digest(row.model_dump(mode="json", exclude={"feature_hash"}))
    values = [0.0] * 31
    values[MODEL_INPUT_NAMES.index("wind_ms")] = -0.2
    values[MODEL_INPUT_NAMES.index("wind_ms_missing")] = 0.1
    values[0] = 0.05
    exp = forest_explanation(row, values, 0.5, 0.45, False, model.numeric.medians)
    assert len(exp["reasons"]) == 3
    assert "missing; training-median imputation" in exp["reasons"][0]
    assert "lowers" in exp["reasons"][0] and "missing indicator is set" in exp["reasons"][1]
    assert all("Observed wind" not in text for text in exp["reasons"])
    assert (
        "lowers"
        in forest_explanation(row, [-0.1] + [0.0] * 30, 0.5, 0.4, False, model.numeric.medians)[
            "reasons"
        ][0]
    )


def test_uncalibrated_model_has_null_probability_and_same_shap_units(fitted, tmp_path):
    data, original, thresholds = fitted
    model = original.model_copy(
        deep=True,
        update={"sigmoid": None, "probability_supported": False, "calibration_status": "rejected"},
    )
    thresholds = thresholds.model_copy(update={"score_units": "forest_vote_fraction"})
    reference = bundle(tmp_path / "raw", model, thresholds)
    scored = score_features(
        [data.samples[0].target.feature], reference.scorer_version, approved=reference
    )[0]
    assert scored.score_kind == "model_score" and scored.escalation_probability is None
    assert scored.priority_score == model.predict([data.samples[0].target.feature])[0].model_score
    assert "uncalibrated_model_score_probability_unavailable" in scored.warnings


def test_process_cache_is_single_load_under_concurrency(fitted, approved, monkeypatch):
    import firewatch.ml.predict as scoring

    original, calls = scoring.load_bundle, []

    def counted(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(scoring, "load_bundle", counted)
    row = fitted[0].samples[0].target.feature
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(
            pool.map(
                lambda _: score_features([row], approved.scorer_version, approved=approved),
                range(8),
            )
        )
    assert len(calls) == 1
    assert len({o[0].priority_score for o in outputs}) == 1
    # Cached immutable snapshot stays pinned; changes require a new approved digest/process.
    (approved.artifact_path / "model.json").write_text("corrupted")
    assert score_features([row], approved.scorer_version, approved=approved)
    scoring._MODEL_CACHE.clear()
    with pytest.raises(ValueError, match="checksum"):
        score_features([row], approved.scorer_version, approved=approved)


def test_no_approval_version_mismatch_or_silent_fallback(fitted, approved):
    row = fitted[0].samples[0].target.feature
    with pytest.raises(ValueError, match="approved"):
        score_features([row], "candidate")
    with pytest.raises(ValueError, match="differs"):
        score_features([row], "other", approved=approved)
    with pytest.raises(ValueError, match="Baseline"):
        score_features([row], "baseline-v1", approved=approved)
    with pytest.raises(ValueError):
        ApprovedScorer.model_validate(approved.model_dump() | {"approval_status": "candidate"})
    assert score_features([], approved.scorer_version, approved=approved) == []


@pytest.mark.parametrize(
    "change", ["nan", "inf", "version", "flags", "hash", "units", "unknown", "config"]
)
def test_mutated_input_is_rejected_before_artifact_loading(fitted, approved, monkeypatch, change):
    import firewatch.ml.predict as scoring

    row = fitted[0].samples[0].target.feature.model_copy(deep=True)
    if change in {"nan", "inf"}:
        row.values["wind_ms"] = float(change)
    elif change == "version":
        row.feature_version = "features-v2"
    elif change == "flags":
        row.missing_flags["wind_ms"] = True
    elif change == "hash":
        row.feature_hash = "0" * 64
    elif change == "units":
        row.units["wind_ms"] = "knots"
    elif change == "unknown":
        row.values["tomorrow_frp"] = 999.0
    else:
        row.lineage["config"]["weather_max_age_hours"] = 7
        row.feature_hash = digest(row.model_dump(mode="json", exclude={"feature_hash"}))

    def denied(*args):
        raise ValueError(
            "Model configuration mismatch" if change == "config" else "Unexpected load"
        )

    if change != "config":
        monkeypatch.setattr(scoring, "load_bundle", denied)
    with pytest.raises(ValueError):
        score_features([row], approved.scorer_version, approved=approved)


def test_permuted_mapping_uses_exact_manifest_order(fitted, approved):
    row = fitted[0].samples[0].target.feature.model_copy(deep=True)
    reference = score_features([row], approved.scorer_version, approved=approved)[0]
    row.values = dict(reversed(list(row.values.items())))
    actual = score_features([row], approved.scorer_version, approved=approved)[0]
    assert actual.priority_score == reference.priority_score
    assert actual.explanation == reference.explanation


def test_duplicate_oversized_missing_required_and_past_scoring_time(fitted, approved):
    row = fitted[0].samples[0].target.feature
    assert len(score_features([row, row], approved.scorer_version, approved=approved)) == 2
    with pytest.raises(ValueError):
        score_features([row] * 257, approved.scorer_version, approved=approved)
    missing = row.model_copy(deep=True)
    missing.values["frp_max_mw"] = None
    missing.missing_flags["frp_max_mw"] = True
    missing.feature_hash = digest(missing.model_dump(mode="json", exclude={"feature_hash"}))
    with pytest.raises(ValueError, match="Required"):
        score_features([missing], approved.scorer_version, approved=approved)
    with pytest.raises(ValueError, match="Scoring time"):
        score_features(
            [row],
            approved.scorer_version,
            approved=approved,
            scored_at=row.as_of - timedelta(hours=1),
        )


@pytest.mark.parametrize(
    "change",
    [
        "model",
        "thresholds",
        "order",
        "scale",
        "covers",
        "dependencies",
        "traversal",
        "duplicate_json",
    ],
)
def test_bundle_tampering_and_semantic_mismatch_rejected(fitted, approved, change):
    root = approved.artifact_path
    manifest = json.loads((root / "manifest.json").read_bytes())
    name = (
        "model.json"
        if change in {"model", "covers"}
        else "thresholds.json"
        if change in {"thresholds", "scale"}
        else "feature-contract.json"
    )
    if change in {"dependencies", "traversal", "duplicate_json"}:
        if change == "dependencies":
            manifest["dependencies"]["numpy"] = "other"
        elif change == "traversal":
            manifest["files"]["../unsafe"] = dict(size=0, sha256="0" * 64)
        else:
            raw = (root / "manifest.json").read_text()
            (root / "manifest.json").write_text(
                raw.replace('"schema_version":', '"schema_version":"bad","schema_version":', 1)
            )
            sha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
            with pytest.raises(ValueError, match="Duplicate"):
                load_bundle(root, sha)
            return
    else:
        payload = json.loads((root / name).read_bytes())
        if change == "model":
            payload["feature_names"].reverse()
        elif change == "thresholds":
            payload["fit_row_ids"] = fitted[1].fit_row_ids
        elif change == "covers":
            payload["trees"][0]["nodes"][0]["weighted_samples"] *= 2
        elif change == "scale":
            payload["score_units"] = "MW"
        else:
            payload["model_input_names"].reverse()
        raw = json.dumps(payload).encode()
        (root / name).write_bytes(raw)
        manifest["files"][name] = dict(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    (root / "manifest.json").write_text(json.dumps(manifest))
    sha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        load_bundle(root, sha)


def test_compact_bundle_verifies_cli_without_refitting_or_loading_pickle(approved, monkeypatch):
    import joblib
    from sklearn.ensemble import RandomForestClassifier
    from sqlalchemy.engine import Engine

    def denied(*args, **kwargs):
        raise AssertionError("Runtime attempted retraining, database or executable model loading")

    monkeypatch.setattr(RandomForestClassifier, "fit", denied)
    monkeypatch.setattr(Engine, "connect", denied)
    monkeypatch.setattr(joblib, "load", denied)
    load_bundle(approved.artifact_path, approved.manifest_sha256, approved.scorer_version)
    result = CliRunner().invoke(
        app,
        [
            "verify-inference-bundle",
            "--path",
            str(approved.artifact_path),
            "--manifest-sha256",
            approved.manifest_sha256,
        ],
    )
    assert result.exit_code == 0 and not json.loads(result.stdout)["approval_granted"]
    with pytest.raises(ValueError, match="checksum"):
        load_bundle(approved.artifact_path, "0" * 64)


def test_constant_tree_zero_contributions_and_additivity_failure(fitted):
    data, template, _ = fitted
    leaf = ForestNode(
        left=-1,
        right=-1,
        feature=-2,
        threshold=-2,
        class_values=(0.3, 0.7),
        impurity=0.42,
        samples=10,
        weighted_samples=10,
    )
    model = template.model_copy(deep=True, update={"trees": [ForestTree(nodes=[leaf])] * 8})
    shap = PathDependentSHAP(model)
    vector = transformed_inputs(data.samples[0].target.feature, model)
    assert shap.explain(vector, 0.7) == [0.0] * 31
    assert shap.base_value == pytest.approx(0.7)
    tiny = forest_explanation(
        data.samples[0].target.feature, [1e-13] + [0.0] * 30, 0.7, 0.7, False, model.numeric.medians
    )
    assert "display tolerance" in tiny["reasons"][0]
    # Python 3.12 sum() compensates floating-point error, while the serialized
    # reference uses a sequential accumulator. Preserve exact threshold parity.
    from firewatch.ml.inference_bundle import RuntimeForest

    fraction = leaf.model_copy(update={"class_values": (0.9, 0.1)})
    raw = model.model_copy(
        deep=True,
        update={
            "trees": [ForestTree(nodes=[fraction])] * 8,
            "sigmoid": None,
            "probability_supported": False,
            "calibration_status": "rejected",
        },
    )
    thresholds = fitted[2].model_copy(update={"score_units": "forest_vote_fraction"})
    expected = raw.predict([data.samples[0].target.feature])[0].model_score
    runtime = RuntimeForest(raw, thresholds)
    actual, _, _, explanation = runtime.score(data.samples[0].target.feature)
    assert actual == explanation["raw_model_score"] == expected
    with pytest.raises(ValueError, match="additivity"):
        shap.explain(vector, 0.6)
    vector[0] = np.inf
    with pytest.raises(ValueError, match="finite"):
        shap.explain(vector, 0.7)


def test_max_depth_polynomial_explanation_is_additive(fitted):
    _, template, _ = fitted
    nodes = []

    def build(depth, code):
        index = len(nodes)
        nodes.append(None)
        if depth == 12:
            probability = (code % 17) / 16
            node = ForestNode(
                left=-1,
                right=-1,
                feature=-2,
                threshold=-2,
                class_values=(1 - probability, probability),
                impurity=0,
                samples=1,
                weighted_samples=1,
            )
        else:
            left, right = build(depth + 1, code * 2), build(depth + 1, code * 2 + 1)
            probability = (nodes[left].class_values[1] + nodes[right].class_values[1]) / 2
            node = ForestNode(
                left=left,
                right=right,
                feature=depth,
                threshold=0,
                class_values=(1 - probability, probability),
                impurity=0,
                samples=2 ** (12 - depth),
                weighted_samples=2 ** (12 - depth),
            )
        nodes[index] = node
        return index

    build(0, 0)
    tree = ForestTree(nodes=nodes)
    model = template.model_copy(
        deep=True,
        update={
            "trees": [tree] * 8,
            "parameters": template.parameters.model_copy(update={"max_depth": 12}),
        },
    )
    shap = PathDependentSHAP(model)
    vector = np.ones(31, dtype=np.float32)
    raw = weighted_conditional(tree, vector, set(range(12)))
    values = shap.explain(vector, raw)
    assert shap.base_value + sum(values) == pytest.approx(raw, abs=1e-10)
    assert values[12:] == [0.0] * 19


def test_batch_validates_all_inputs_before_loading_model(fitted, approved, monkeypatch):
    import firewatch.ml.predict as scoring

    rows = [r.target.feature.model_copy(deep=True) for r in fitted[0].samples[:2]]
    rows[1].feature_hash = "0" * 64

    def denied(*args):
        raise AssertionError("Invalid batch must fail before artifact loading")

    monkeypatch.setattr(scoring, "load_bundle", denied)
    with pytest.raises(ValueError, match="integrity"):
        score_features(rows, approved.scorer_version, approved=approved)


def test_baseline_terms_and_all_warnings_are_saved_without_shap(fitted):
    row = fitted[0].samples[0].target.feature
    for version, units in (("baseline-v1", "MW"), ("weighted-baseline-v1", "index_points")):
        result = score_features([row], version)[0]
        assert result.explanation["contribution_units"] == units
        assert sum(result.explanation["contributions"].values()) == pytest.approx(
            result.priority_score
        )
        assert result.explanation["warnings"] == result.warnings
        assert result.score_kind == "baseline" and result.escalation_probability is None
        assert "shap" not in result.explanation["method"]
        assert 1 <= len(result.reasons) <= 3


def test_runtime_warnings_are_exposed_by_existing_hotspot_api():
    from datetime import UTC, datetime
    from unittest.mock import MagicMock
    from uuid import uuid4

    from firewatch.api.presentation import hotspot
    from firewatch.schemas.common import Priority, ScoreKind

    snapshot = MagicMock(
        id=uuid4(),
        latitude=0,
        longitude=0,
        member_count=1,
        latest_detection_at=datetime.now(UTC),
        frp_max_mw=20,
        frp_sum_mw=20,
    )
    features = MagicMock(
        lineage={"quality_flags": []}, missing_flags={}, feature_version="features-v1"
    )
    score = MagicMock(
        priority=Priority.MEDIUM,
        priority_score=20,
        score_kind=ScoreKind.BASELINE,
        escalation_probability=None,
        scorer_version="baseline-v1",
        scored_at=datetime.now(UTC),
        explanation={
            "reasons": ["Observed FRP term"],
            "warnings": ["priority_thresholds_provisional"],
        },
    )
    view = hotspot((snapshot, score, features, None))
    assert "priority_thresholds_provisional" in view.quality_flags


@pytest.mark.parametrize("stage", ["development", "final"])
def test_candidate_export_preserves_frozen_evaluation_without_self_approval(
    fitted, tmp_path, monkeypatch, stage
):
    # Unit export control flow: source validation itself is exercised by integration.
    import firewatch.ml.evaluation_package as evaluation_package
    from firewatch.ml.inference_bundle import export_inference_bundle

    _, model, thresholds = fitted
    source = tmp_path / "evaluation"
    base = source if stage == "development" else source / "prepared"
    base.mkdir(parents=True)
    (base / "evaluation-report.json").write_text(
        json.dumps(
            {
                "status": "development_frozen",
                "thresholds": {"forest": thresholds.model_dump(mode="json")},
            }
        )
    )
    # EvaluationModels has additional paired methods. Reuse its legitimate synthetic constructor.
    from forest_factory import small_spec

    from firewatch.ml.evaluation_models import fit_evaluation_models

    models = fit_evaluation_models(fitted[0], model, small_spec())
    (base / "evaluation-models.json").write_text(models.model_dump_json())
    calls = []

    def verified(root, sha):
        calls.append((root, sha))
        return {
            "stage": stage,
            "final_test_status": "sealed" if stage == "development" else "opened_once",
        }

    monkeypatch.setattr(evaluation_package, "verify_evaluation", verified)
    output = tmp_path / "inference"
    result = export_inference_bundle(source, output, "a" * 64, "synthetic-export-v1")
    assert calls == [(source, "a" * 64)] and result["approval_status"] == "candidate"
    assert not result["promotion_performed"]
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["source_evaluation_sha256"] == "a" * 64
    assert set(manifest["files"]) == {"model.json", "thresholds.json", "feature-contract.json"}
    with pytest.raises(ValueError, match="approved"):
        score_features([fitted[0].samples[0].target.feature], "synthetic-export-v1")
    with pytest.raises(ValueError, match="exists"):
        export_inference_bundle(source, output, "a" * 64, "synthetic-export-v1")


def test_artifact_bounds_and_atomic_failure_cleanup(fitted, approved, tmp_path, monkeypatch):
    import firewatch.ml.inference_bundle as inference

    _, model, thresholds = fitted

    def denied(*args):
        raise ValueError("synthetic validation failure")

    monkeypatch.setattr(inference, "load_bundle", denied)
    with pytest.raises(ValueError, match="synthetic"):
        inference._write_bundle(tmp_path / "failed", model, thresholds, "synthetic-v1", "a" * 64)
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".inference-bundle-*"))
    # Manifest reads remain bounded even if its checksum is supplied as trusted.
    manifest = approved.artifact_path / "manifest.json"
    manifest.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="oversized"):
        load_bundle(approved.artifact_path, hashlib.sha256(manifest.read_bytes()).hexdigest())


def test_cache_capacity_fails_without_evicting_pinned_models(fitted, approved, monkeypatch):
    import firewatch.ml.predict as scoring

    row = fitted[0].samples[0].target.feature
    score_features([row], approved.scorer_version, approved=approved)
    loaded = next(iter(scoring._MODEL_CACHE.values()))
    for i in range(7):
        scoring._MODEL_CACHE[(str(i), "a" * 64, str(i))] = loaded
    other = approved.model_copy(update={"artifact_path": approved.artifact_path.parent / "other"})
    with pytest.raises(ValueError, match="cache is full"):
        score_features([row], other.scorer_version, approved=other)
    assert len(scoring._MODEL_CACHE) == 8
    assert score_features([row], approved.scorer_version, approved=approved)
