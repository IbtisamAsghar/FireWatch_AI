"""Bound, relocatable development comparisons; no pickle or deployed training."""

import hashlib
import importlib.metadata
import math
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from firewatch.ml.evaluate import compare_baselines
from firewatch.ml.split_package import _safe_file, _write, read_samples, verify_split_package
from firewatch.ml.splits import SplitResult
from firewatch.pipeline.handoff import _decode_json


def dependencies() -> dict[str, str]:
    return {name: importlib.metadata.version(name) for name in ("numpy", "scikit-learn")}


def export_comparison(source: Path, output: Path, expected_hash: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("A trusted split manifest SHA-256 is required")
    verified = verify_split_package(source, expected_hash)
    split = SplitResult.model_validate_json((source / "membership.json").read_bytes())
    report = compare_baselines(read_samples(source / "labels"), split)
    target = output.resolve()
    if target.exists():
        raise ValueError("Comparison output exists; choose a new directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".baseline-comparison-", dir=target.parent)).resolve()
    try:
        manifest = _decode_json((source / "manifest.json").read_bytes())
        for name in ["manifest.json", *manifest["files"]]:
            destination = staging / "split" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_safe_file(source, name), destination)
        _write(staging / "comparison.json", report)
        _write(
            staging / "manifest.json",
            dict(
                schema_version="baseline-comparison-package-v1",
                split_manifest_sha256=verified["manifest_sha256"],
                dependencies=dependencies(),
                final_test_status="sealed",
                files={
                    p.relative_to(staging).as_posix(): dict(
                        size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
                    )
                    for p in sorted(staging.rglob("*"))
                    if p.is_file()
                },
            ),
        )
        verify_comparison(staging)
        staging.rename(target)
        return report
    finally:
        if staging.exists():
            if staging.parent != target.parent or not staging.name.startswith(
                ".baseline-comparison-"
            ):
                raise ValueError("Refusing cleanup outside owned comparison staging directory")
            shutil.rmtree(staging)


def _same(expected: Any, actual: Any) -> bool:
    # Bound dependencies and manifest bytes remain exact; permit native BLAS roundoff
    # only in regenerated numeric report fields, never in identities or inventories.
    if (
        isinstance(expected, float)
        and isinstance(actual, (int, float))
        and not isinstance(actual, bool)
    ):
        return math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-10)
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict):
        return expected.keys() == actual.keys() and all(
            _same(v, actual[k]) for k, v in expected.items()
        )
    if isinstance(expected, list):
        return len(expected) == len(actual) and all(
            _same(a, b) for a, b in zip(expected, actual, strict=True)
        )
    return bool(expected == actual)


def verify_comparison(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    try:
        return _verify(root, expected_hash)
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("Malformed baseline comparison package") from None


def _verify(root: Path, expected_hash: str | None) -> dict[str, Any]:
    path = _safe_file(root, "manifest.json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Oversized comparison manifest")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected_hash and sha != expected_hash:
        raise ValueError("Comparison manifest checksum mismatch")
    manifest = _decode_json(raw)
    if (
        manifest["schema_version"] != "baseline-comparison-package-v1"
        or manifest["dependencies"] != dependencies()
        or manifest["final_test_status"] != "sealed"
    ):
        raise ValueError("Comparison contract/dependency mismatch")
    if not {"comparison.json", "split/manifest.json"} <= set(manifest["files"]):
        raise ValueError("Missing bound comparison files")
    total = 0
    for name, entry in manifest["files"].items():
        file = _safe_file(root, name)
        size = file.stat().st_size
        total += size
        if (
            size > 64 * 1024 * 1024
            or total > 256 * 1024 * 1024
            or size != entry["size"]
            or hashlib.sha256(file.read_bytes()).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Comparison file checksum/size mismatch")
    verify_split_package(root / "split", manifest["split_manifest_sha256"])
    nested = _decode_json((root / "split/manifest.json").read_bytes())
    if set(manifest["files"]) != {"comparison.json", "split/manifest.json"} | {
        "split/" + name for name in nested["files"]
    }:
        raise ValueError("Comparison file inventory mismatch")
    result = SplitResult.model_validate_json((root / "split/membership.json").read_bytes())
    report = compare_baselines(read_samples(root / "split/labels"), result)
    if not _same(report, _decode_json((root / "comparison.json").read_bytes())):
        raise ValueError("Comparison differs from deterministic recomputation")
    return dict(
        verified=True,
        manifest_sha256=sha,
        status=report["status"],
        cohort_count=report["cohort_count"],
        logistic_status=report["logistic_status"],
        final_test_status="sealed",
    )
