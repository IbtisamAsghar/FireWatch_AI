"""Synthetic registry authority stubs for SQL/worker tests, not scientific approval evidence."""

import hashlib
import json
from pathlib import Path

from firewatch.ml.inference_bundle import _write_bundle
from firewatch.ml.registry_artifacts import ApprovalReview, PromotionCandidate


def make_candidate(model_dir, fitted, version="synthetic-forest-v1", region=None, modes=None):
    _, model, thresholds = fitted
    root = Path(model_dir).resolve() / "releases" / version
    sha = _write_bundle(root / "bundle", model, thresholds, version, "a" * 64)
    review = ApprovalReview(
        decision="approve",
        reviewer="synthetic-independent-reviewer",
        model_author="synthetic-test-author",
        independent_review=True,
        limitations_acknowledged=True,
        evaluation_manifest_sha256="a" * 64,
        inference_manifest_sha256=sha,
        model_card_sha256="b" * 64,
        rationale="Synthetic authority stub exercises registry transactions, not real performance.",
        supported_modes=modes or ["replay"],
        live_generalization_reviewed=True,
    )
    (root / "review.json").write_text(review.model_dump_json())
    manifest = dict(
        schema_version="managed-model-release-v1",
        version=version,
        bundle_sha256=sha,
        evaluation_sha256="a" * 64,
        files={
            p.relative_to(root).as_posix(): dict(
                size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
            )
            for p in root.rglob("*")
            if p.is_file()
        },
    )
    raw = json.dumps(manifest).encode()
    (root / "release-manifest.json").write_bytes(raw)
    target = region or dict(
        region="northern-california", bbox=dict(west=-122.5, south=38.5, east=-120.0, north=40.5)
    )
    target = target | {"target_spec_sha256": model.target_spec_sha256}
    return PromotionCandidate(
        version,
        sha,
        "a" * 64,
        root,
        hashlib.sha256(raw).hexdigest(),
        model.probability_supported,
        review,
        {"synthetic_authority_stub": True},
        target,
    )
