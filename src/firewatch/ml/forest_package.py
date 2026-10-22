"""Bound forest training packages with safe JSON estimator/preprocessor/calibrator data."""

import hashlib
import importlib.metadata
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from firewatch.ml.comparison_package import _same
from firewatch.ml.events import EventCatalog, EventReview
from firewatch.ml.forest_artifact import PortableForest
from firewatch.ml.forest_spec import ForestSpec, load_forest_spec
from firewatch.ml.forest_training import ForestData, train_forest
from firewatch.ml.split_package import _safe_file, _write, read_samples, verify_split_package
from firewatch.ml.splits import SplitResult, SplitSpec
from firewatch.pipeline.handoff import _decode_json


def dependencies() -> dict[str, str]:
    return {
        name: importlib.metadata.version(name)
        for name in ("numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl", "pydantic")
    } | {"python": ".".join(str(i) for i in sys.version_info[:3])}


def read_forest_data(root: Path) -> ForestData:
    raw = _decode_json((root / "event-review.json").read_bytes())
    return ForestData(
        read_samples(root / "labels"),
        SplitResult.model_validate_json((root / "membership.json").read_bytes()),
        SplitSpec.model_validate_json((root / "split-spec.json").read_bytes()),
        EventCatalog.model_validate_json((root / "event-catalog.json").read_bytes()),
        EventReview.model_validate(raw) if raw is not None else None,
    )


def export_forest(
    source: Path, output: Path, source_sha256: str, spec_path: Path = Path("configs/forest-v1.yaml")
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        raise ValueError("A trusted split manifest SHA-256 is required")
    verify_split_package(source, source_sha256)
    spec = load_forest_spec(spec_path)
    run = train_forest(read_forest_data(source), spec)
    target = output.resolve()
    if target.exists():
        raise ValueError("Forest output exists; choose a new directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".forest-training-", dir=target.parent)).resolve()
    try:
        manifest = _decode_json((source / "manifest.json").read_bytes())
        for name in ["manifest.json", *manifest["files"]]:
            destination = staging / "split" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_safe_file(source, name), destination)
        _write(staging / "training-spec.json", spec.model_dump(mode="json"))
        _write(staging / "training-report.json", run.report)
        _write(staging / "model.json", run.model.model_dump(mode="json") if run.model else None)
        _write(
            staging / "manifest.json",
            dict(
                schema_version="forest-training-package-v1",
                split_manifest_sha256=source_sha256,
                dependencies=dependencies(),
                final_test_status="sealed",
                model_available=run.model is not None,
                executable_serialization=False,
                files={
                    p.relative_to(staging).as_posix(): dict(
                        size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
                    )
                    for p in sorted(staging.rglob("*"))
                    if p.is_file()
                },
            ),
        )
        verify_forest(staging)
        staging.rename(target)
        return run.report
    finally:
        if staging.exists():
            if staging.parent != target.parent or not staging.name.startswith(".forest-training-"):
                raise ValueError("Refusing cleanup outside owned forest staging directory")
            shutil.rmtree(staging)


def verify_forest(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    try:
        return _verify(root, expected_hash)
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("Malformed forest training package") from None


def _verify(root: Path, expected_hash: str | None) -> dict[str, Any]:
    path = _safe_file(root, "manifest.json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Oversized forest manifest")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected_hash is not None and (
        not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or sha != expected_hash
    ):
        raise ValueError("Forest manifest checksum mismatch")
    manifest = _decode_json(raw)
    if (
        manifest["schema_version"] != "forest-training-package-v1"
        or manifest["dependencies"] != dependencies()
        or manifest["final_test_status"] != "sealed"
        or manifest["executable_serialization"] is not False
    ):
        raise ValueError("Forest package contract/dependency mismatch")
    required = {"training-spec.json", "training-report.json", "model.json", "split/manifest.json"}
    if not required <= set(manifest["files"]):
        raise ValueError("Missing bound forest package file")
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
            raise ValueError("Forest file checksum/size mismatch")
    verify_split_package(root / "split", manifest["split_manifest_sha256"])
    nested = _decode_json((root / "split/manifest.json").read_bytes())
    if set(manifest["files"]) != required | {"split/" + name for name in nested["files"]}:
        raise ValueError("Forest file inventory mismatch")
    spec = ForestSpec.model_validate_json((root / "training-spec.json").read_bytes())
    run = train_forest(read_forest_data(root / "split"), spec)
    saved_raw = _decode_json((root / "model.json").read_bytes())
    saved = PortableForest.model_validate(saved_raw) if saved_raw is not None else None
    if (
        not _same(run.report, _decode_json((root / "training-report.json").read_bytes()))
        or not _same(
            run.model.model_dump(mode="json") if run.model else None,
            saved.model_dump(mode="json") if saved else None,
        )
        or manifest["model_available"] != (run.model is not None)
    ):
        raise ValueError("Forest differs from deterministic source-bound recomputation")
    return dict(
        verified=True,
        manifest_sha256=sha,
        status=run.report["status"],
        model_available=run.model is not None,
        probability_supported=run.report["probability_supported"],
        calibration_status=run.report["calibration"]["status"],
        final_test_status="sealed",
    )


def load_trusted_forest(root: Path, expected_hash: str) -> PortableForest:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("A trusted forest manifest SHA-256 is required")
    result = verify_forest(root, expected_hash)
    if not result["model_available"]:
        raise ValueError("No forest was fitted; retain baseline scoring")
    return PortableForest.model_validate_json(_safe_file(root, "model.json").read_bytes())
