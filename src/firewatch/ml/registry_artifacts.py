"""Offline release validation, human review attestation and immutable managed copies."""

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from firewatch.ml.baselines import BASELINE_VERSIONS
from firewatch.ml.inference_bundle import _json, load_bundle
from firewatch.schemas.common import DTO, Digest, RunMode
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.firms_normalize import digest

REGION = "northern-california"
BBOX = BoundingBox(west=-122.5, south=38.5, east=-120, north=40.5)


def valid_version(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9._-]{0,99}", value)) and value != "registry"


class ApprovalReview(DTO):
    schema_version: Literal["model-approval-review-v1"] = "model-approval-review-v1"
    decision: Literal["approve"]
    reviewer: str = Field(min_length=3, max_length=100)
    model_author: str = Field(min_length=3, max_length=100)
    independent_review: Literal[True]
    limitations_acknowledged: Literal[True]
    evaluation_manifest_sha256: Digest
    inference_manifest_sha256: Digest
    model_card_sha256: Digest
    rationale: str = Field(min_length=30, max_length=2000)
    supported_modes: list[RunMode] = Field(
        default_factory=lambda: [RunMode.REPLAY], min_length=1, max_length=2
    )
    live_generalization_reviewed: bool = False

    @model_validator(mode="after")
    def independent(self) -> Self:
        if (
            self.reviewer.strip().casefold() == self.model_author.strip().casefold()
            or any(
                not s.strip() or "REPLACE" in s.upper()
                for s in (self.reviewer, self.model_author, self.rationale)
            )
            or len(set(self.supported_modes)) != len(self.supported_modes)
        ):
            raise ValueError("Review requires distinct named author/reviewer and actual rationale")
        if RunMode.LIVE in self.supported_modes and not self.live_generalization_reviewed:
            raise ValueError("Live use requires an explicit generalization review")
        return self


@dataclass(frozen=True)
class PromotionCandidate:
    version: str
    bundle_sha256: str
    evaluation_sha256: str
    release_root: Path
    release_sha256: str
    probability_supported: bool
    review: ApprovalReview
    metrics: dict[str, Any]
    target: dict[str, Any]


def _read(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Release evidence must be a regular file")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Release evidence exceeds its byte bound")
    return raw


def _write(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )


def managed_path(root: Path, relative: str) -> Path:
    base = root.resolve()
    supplied = Path(relative)
    if supplied.is_absolute() or ".." in supplied.parts:
        raise ValueError("Model artifacts must stay inside the managed model directory")
    path = base / supplied
    if path.resolve() == base or not path.resolve().is_relative_to(base):
        raise ValueError("Model artifact escapes the managed directory")
    current = base
    for part in supplied.parts:
        current /= part
        if current.is_symlink():
            raise ValueError("Managed model paths must not traverse symlinks")
    return path


def _copy_package(source: Path, destination: Path, expected_sha: str) -> None:
    from firewatch.ml.split_package import _safe_file

    raw = _read(source / "manifest.json", 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("Source changed while preparing promotion")
    manifest = _json(raw)
    total = 0
    destination.mkdir(parents=True)
    (destination / "manifest.json").write_bytes(raw)
    for name, entry in manifest["files"].items():
        payload = _read(_safe_file(source, name), 64 * 1024 * 1024)
        total += len(payload)
        if (
            total > 256 * 1024 * 1024
            or len(payload) != entry["size"]
            or hashlib.sha256(payload).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Source changed while preparing promotion")
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)


def verify_managed_release(root: Path, expected_sha: str) -> dict[str, Any]:
    from firewatch.ml.split_package import _safe_file

    raw = _read(root / "release-manifest.json", 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError("Managed release manifest checksum mismatch")
    manifest = _json(raw)
    if manifest["schema_version"] != "managed-model-release-v1":
        raise ValueError("Unsupported managed release")
    total = 0
    for name, entry in manifest["files"].items():
        payload = _read(_safe_file(root, name), 64 * 1024 * 1024)
        total += len(payload)
        if (
            total > 320 * 1024 * 1024
            or len(payload) != entry["size"]
            or hashlib.sha256(payload).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Managed model release integrity failed")
    load_bundle(root / "bundle", manifest["bundle_sha256"], manifest["version"])
    return dict(manifest)


def prepare_promotion(
    model_dir: Path,
    version: str,
    evaluation: Path,
    evaluation_sha256: str,
    bundle: Path,
    bundle_sha256: str,
    review_file: Path,
) -> PromotionCandidate:
    from firewatch.ml.evaluation_models import EvaluationModels
    from firewatch.ml.evaluation_package import verify_evaluation
    from firewatch.ml.forest_artifact import PortableForest
    from firewatch.ml.priority_thresholds import PriorityThresholds

    if not valid_version(version) or version in BASELINE_VERSIONS:
        raise ValueError("Promotion requires a new supervised version")
    verified = verify_evaluation(evaluation, evaluation_sha256)
    if verified["stage"] != "final":
        raise ValueError("Promotion requires completed final-test release evaluation")
    report = _json(_read(evaluation / "evaluation-report.json", 64 * 1024 * 1024))
    if (
        report["promotion_recommendation"] != "independent_review_candidate"
        or not report["final_test_evaluated"]
        or report["final_test_status"] != "opened_once"
    ):
        raise ValueError("Held-out ranking evidence does not justify ML promotion")
    final = report["final"]
    forest_metrics = final["methods"]["forest"]["metrics"]
    uncertainty = final["uncertainty"]
    if (
        forest_metrics["events"] < 5
        or min(forest_metrics["positive"], forest_metrics["negative"]) < 10
        or uncertainty["status"] != "estimated"
        or uncertainty["repetitions"] < 20
        or uncertainty["resampling_unit"] != "whole_partition_event"
    ):
        raise ValueError("Final event/class/uncertainty evidence is insufficient")
    for name in BASELINE_VERSIONS:
        interval = uncertainty["paired_deltas"].get("forest_minus_" + name, {})
        if (
            interval.get("lower") is None
            or interval["lower"] <= 0
            or interval.get("valid_replicates", 0) < 0.9 * uncertainty["repetitions"]
            or forest_metrics["average_precision"]
            <= final["methods"][name]["metrics"]["average_precision"]
        ):
            raise ValueError("Paired final ranking evidence does not improve on both baselines")
    review = ApprovalReview.model_validate(_json(_read(review_file, 65536)))
    card_sha = hashlib.sha256(_read(evaluation / "model-card.md", 1024 * 1024)).hexdigest()
    if (
        review.evaluation_manifest_sha256 != evaluation_sha256
        or review.inference_manifest_sha256 != bundle_sha256
        or review.model_card_sha256 != card_sha
    ):
        raise ValueError("Human review does not bind these exact artifacts and model card")
    load_bundle(bundle, bundle_sha256, version)
    bundle_manifest = _json(_read(bundle / "manifest.json", 65536))
    if bundle_manifest["source_evaluation_sha256"] != evaluation_sha256:
        raise ValueError("Candidate bundle must be derived from this final evaluation")
    expected = EvaluationModels.model_validate_json(
        _read(evaluation / "prepared/evaluation-models.json", 64 * 1024 * 1024)
    )
    model = PortableForest.model_validate_json(_read(bundle / "model.json", 32 * 1024 * 1024))
    thresholds = PriorityThresholds.model_validate_json(
        _read(bundle / "thresholds.json", 1024 * 1024)
    )
    if (
        model != expected.forest
        or thresholds.model_dump(mode="json") != report["thresholds"]["forest"]
    ):
        raise ValueError("Inference model/thresholds differ from evaluated candidate")
    # Final verifier recomputes comparisons, intervals, class/event gates and the model card.
    final = report["final"]
    metrics = dict(
        forest=final["methods"]["forest"]["metrics"],
        baselines={n: final["methods"][n]["metrics"] for n in BASELINE_VERSIONS},
        uncertainty=final["uncertainty"],
        recommendation=report["promotion_recommendation"],
        final_test_status="opened_once",
    )
    target = dict(
        target_spec_sha256=model.target_spec_sha256,
        region=REGION,
        bbox=BBOX.model_dump(mode="json"),
    )
    root = managed_path(model_dir, f"releases/{version}/{bundle_sha256}")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".registry-stage-", dir=root.parent)).resolve()
    try:
        _copy_package(bundle, staging / "bundle", bundle_sha256)
        _copy_package(evaluation, staging / "evaluation", evaluation_sha256)
        _write(staging / "review.json", review.model_dump(mode="json"))
        _write(
            staging / "release-manifest.json",
            dict(
                schema_version="managed-model-release-v1",
                version=version,
                bundle_sha256=bundle_sha256,
                evaluation_sha256=evaluation_sha256,
                review_sha256=digest(review.model_dump(mode="json")),
                files={
                    file.relative_to(staging).as_posix(): dict(
                        size=file.stat().st_size,
                        sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
                    )
                    for file in sorted(staging.rglob("*"))
                    if file.is_file()
                },
            ),
        )
        sha = hashlib.sha256((staging / "release-manifest.json").read_bytes()).hexdigest()
        verify_managed_release(staging, sha)
        if root.exists():
            verify_managed_release(root, sha)
        else:
            staging.rename(root)
        return PromotionCandidate(
            version,
            bundle_sha256,
            evaluation_sha256,
            root,
            sha,
            model.probability_supported,
            review,
            metrics,
            target,
        )
    finally:
        if staging.exists():
            if staging.parent != root.parent or not staging.name.startswith(".registry-stage-"):
                raise ValueError("Refusing cleanup outside owned registry staging")
            shutil.rmtree(staging)
