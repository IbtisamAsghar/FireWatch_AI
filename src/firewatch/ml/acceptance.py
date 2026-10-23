"""Portable baseline evidence without a new experiment, holdout claim or ML approval."""

import hashlib
import re
from pathlib import Path
from typing import Any

from firewatch.ml.comparison_package import verify_comparison
from firewatch.ml.evaluation_package import _cleanup, _copy, _stage, verify_evaluation
from firewatch.ml.split_package import _safe_file, _write
from firewatch.ml.splits import SplitResult
from firewatch.pipeline.handoff import _decode_json

SCHEMA = "baseline-release-v1"


def _hash(value: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("A trusted manifest SHA-256 is required")


def _verify_development(root: Path, expected_hash: str) -> dict[str, Any]:
    # Refuse final/fitted evidence before invoking any recursive recomputation.
    path = _safe_file(root, "manifest.json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Oversized evaluation manifest")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("Evaluation manifest checksum mismatch")
    manifest = _decode_json(raw)
    if manifest.get("stage") != "development" or manifest.get("final_test_status") != "sealed":
        raise ValueError("Baseline release requires sealed development evidence")
    training_path = _safe_file(root, "training/manifest.json")
    if training_path.stat().st_size > 1024 * 1024:
        raise ValueError("Oversized training manifest")
    training_raw = training_path.read_bytes()
    pin = manifest["files"]["training/manifest.json"]
    if (
        len(training_raw) != pin["size"]
        or hashlib.sha256(training_raw).hexdigest() != pin["sha256"]
    ):
        raise ValueError("Training manifest checksum mismatch")
    if _decode_json(training_raw).get("model_available") is not False:
        raise ValueError("Baseline release requires no fitted forest")
    return verify_evaluation(root, expected_hash)


def _outputs(root: Path, evaluation_sha: str, comparison_sha: str) -> dict[str, bytes]:
    evaluation = _verify_development(root / "evaluation", evaluation_sha)
    comparison = verify_comparison(root / "comparison", comparison_sha)
    report = _decode_json((root / "evaluation/evaluation-report.json").read_bytes())
    if evaluation["stage"] != "development":
        raise ValueError("Baseline release requires sealed development evidence")
    training = root / "evaluation/training"
    training_manifest = _decode_json((training / "manifest.json").read_bytes())
    comparison_manifest = _decode_json((root / "comparison/manifest.json").read_bytes())
    if (
        evaluation["stage"] != "development"
        or evaluation["status"] != "insufficient_data"
        or evaluation["final_test_status"] != "sealed"
        or report["release_recommendation"] != "baseline_only"
        or training_manifest["model_available"]
        or _decode_json((training / "model.json").read_bytes()) is not None
        or comparison_manifest["split_manifest_sha256"]
        != training_manifest["split_manifest_sha256"]
        or comparison["final_test_status"] != "sealed"
    ):
        raise ValueError("Baseline release requires matching sealed insufficient-data evidence")
    split = SplitResult.model_validate_json((training / "split/membership.json").read_bytes())
    facts = dict(
        schema_version=SCHEMA,
        release_kind="documented_baseline",
        recommended_scorer="baseline-v1",
        alternative_scorer="weighted-baseline-v1",
        activation_performed=False,
        approval_status="baseline_only_no_approved_ml_bundle",
        approved_bundle=None,
        approved_bundle_reason="No eligible fitted forest or completed final evaluation",
        probability=None,
        held_out_ml_metrics=None,
        final_test_status="sealed",
        source_evaluation_manifest_sha256=evaluation_sha,
        source_comparison_manifest_sha256=comparison_sha,
        training_manifest_sha256=hashlib.sha256(
            (training / "manifest.json").read_bytes()
        ).hexdigest(),
        split_manifest_sha256=training_manifest["split_manifest_sha256"],
        dataset_sha256=split.dataset_sha256,
        diagnostic_rows=len(split.membership),
        partition_counts=split.partition_counts,
        exclusion_counts=split.exclusion_counts,
        prototype_thresholds=report["prototype_thresholds"],
        limitations=[
            "Later satellite-observed FRP strengthening is a proxy, not physical fire severity",
            "Clouds, overpass timing and coverage gaps can hide later activity",
            "Thermal event groups and repeated snapshots are not independent verified fires",
            "Source products, weather availability and region constrain transfer",
            "Baseline priorities are provisional research categories with null probability",
            "Synthetic fixtures establish software behavior, not real predictive performance",
            "No approved ML bundle or supported real held-out ML metrics are available",
        ],
    )
    # Canonical output permits independent verification after moving the whole package.
    from firewatch.evidence.canonical import canonical_bytes

    card = (
        "# FireWatch documented baseline release\n\n"
        "Release: baseline-v1 (FRP maximum ranking); weighted-baseline-v1 is an explicit "
        "alternative. Neither scorer estimates probability.\n\n"
        f"Diagnostic rows: {facts['diagnostic_rows']}. "
        f"Exclusions: {split.exclusion_counts}.\n\n"
        "The evaluation and training manifests, frozen source policies, labels, event review "
        "and split membership are retained in evaluation/. Baseline comparisons and their "
        "identical split provenance are retained in comparison/. "
        "Candidate evaluation card: [evaluation/model-card.md](evaluation/model-card.md).\n\n"
        "An approved ML bundle is unavailable because the source data cannot support a "
        "fitted forest and completed final evaluation. No approval or activation is implied. "
        "The final holdout remains sealed.\n\n"
        "## Intended use and limitations\n\n"
        + "\n".join("- " + item for item in facts["limitations"])
        + "\n\n## Reproduction\n\n"
        "Use the locked Python 3.12 environment and commands in REPRODUCE.md. "
        "Keep manifest.json SHA-256 through a trusted separate channel. "
        "This package contains source observations; share only with authorized recipients.\n"
    )
    commands = (
        "# Reproduce the baseline release\n\n"
        "Run from this package directory in the repository's locked Python 3.12 environment. "
        "Use fresh output directories; commands refuse to overwrite. Verification is offline "
        "and never claims the final-test ledger or accesses the registry.\n\n"
        "```powershell\n"
        f"firewatch verify-evaluation --path evaluation --manifest-sha256 {evaluation_sha}\n"
        "firewatch verify-baseline-comparison --path comparison "
        f"--manifest-sha256 {comparison_sha}\n"
        "firewatch train --splits evaluation/training/split "
        f"--manifest-sha256 {training_manifest['split_manifest_sha256']} "
        "--spec evaluation/training/training-spec.json --output ../reproduced-training\n"
        "# Record the new training/manifest.json SHA256, then evaluate development only:\n"
        "$trainingSha = (Get-FileHash ../reproduced-training/manifest.json -Algorithm SHA256)"
        ".Hash.ToLowerInvariant()\n"
        "firewatch evaluate --training ../reproduced-training --manifest-sha256 $trainingSha "
        "--spec evaluation/evaluation-spec.json --output ../reproduced-evaluation\n"
        "```\n\n"
        "No final evaluation, inference bundle construction or promotion is part of this "
        "baseline handoff. Future eligible ML releases must follow the independent review "
        "and final-evaluation gates in docs/model-registry-and-promotion.md. "
        "Export does not change ACTIVE_SCORER.\n"
    )
    return {
        "release-report.json": canonical_bytes(facts) + b"\n",
        "model-card.md": card.encode("utf-8"),
        "REPRODUCE.md": commands.encode("utf-8"),
    }


def export_baseline_release(
    evaluation: Path, comparison: Path, output: Path, evaluation_sha256: str, comparison_sha256: str
) -> dict[str, Any]:
    _hash(evaluation_sha256)
    _hash(comparison_sha256)
    if any(output.resolve().is_relative_to(p.resolve()) for p in (evaluation, comparison)):
        raise ValueError("Release output must be outside its source packages")
    # Authenticate manifests and all nested files before copying source bytes.
    _verify_development(evaluation, evaluation_sha256)
    verify_comparison(comparison, comparison_sha256)
    target, staging = _stage(output)
    try:
        (staging / "evaluation").mkdir()
        (staging / "comparison").mkdir()
        _copy(evaluation, staging / "evaluation")
        _copy(comparison, staging / "comparison")
        for name, raw in _outputs(staging, evaluation_sha256, comparison_sha256).items():
            (staging / name).write_bytes(raw)
        _write(
            staging / "manifest.json",
            dict(
                schema_version=SCHEMA,
                evaluation_manifest_sha256=evaluation_sha256,
                comparison_manifest_sha256=comparison_sha256,
                files={
                    p.relative_to(staging).as_posix(): dict(
                        size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
                    )
                    for p in sorted(staging.rglob("*"))
                    if p.is_file()
                },
            ),
        )
        result = verify_baseline_release(staging)
        staging.rename(target)
        return result
    finally:
        _cleanup(staging, target)


def verify_baseline_release(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    try:
        return _verify(root, expected_hash)
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("Malformed baseline release") from None


def _verify(root: Path, expected_hash: str | None) -> dict[str, Any]:
    manifest_path = _safe_file(root, "manifest.json")
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError("Oversized baseline release manifest")
    raw = manifest_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected_hash is not None:
        _hash(expected_hash)
        if expected_hash != sha:
            raise ValueError("Baseline release manifest checksum mismatch")
    manifest = _decode_json(raw)
    if manifest["schema_version"] != SCHEMA:
        raise ValueError("Unsupported baseline release schema")
    _hash(manifest["evaluation_manifest_sha256"])
    _hash(manifest["comparison_manifest_sha256"])
    total = 0
    for name, entry in manifest["files"].items():
        path = _safe_file(root, name)
        size = path.stat().st_size
        total += size
        if (
            size > 64 * 1024 * 1024
            or total > 512 * 1024 * 1024
            or size != entry["size"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Baseline release file checksum/size mismatch")
    outputs = _outputs(
        root, manifest["evaluation_manifest_sha256"], manifest["comparison_manifest_sha256"]
    )
    names = set(outputs)
    for prefix in ("evaluation", "comparison"):
        nested = _decode_json(_safe_file(root, prefix + "/manifest.json").read_bytes())
        names |= {prefix + "/manifest.json", *(prefix + "/" + n for n in nested["files"])}
    if set(manifest["files"]) != names:
        raise ValueError("Baseline release inventory mismatch")
    for name, expected in outputs.items():
        if _safe_file(root, name).read_bytes() != expected:
            raise ValueError("Baseline release claim differs from source evidence")
    if any(p.is_symlink() for p in root.rglob("*")) or {
        p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
    } != names | {"manifest.json"}:
        raise ValueError("Unexpected baseline release files")
    report = _decode_json(outputs["release-report.json"])
    return dict(
        verified=True,
        manifest_sha256=sha,
        release_kind=report["release_kind"],
        diagnostic_rows=report["diagnostic_rows"],
        approved_bundle=None,
        final_test_status="sealed",
        activation_performed=False,
    )
