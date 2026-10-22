"""Source-bound evaluation packages and a durable one-shot release export."""

import hashlib
import importlib.metadata
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from firewatch.ml.comparison_package import _same
from firewatch.ml.evaluation_artifacts import write_model_card
from firewatch.ml.evaluation_models import EvaluationModels
from firewatch.ml.evaluation_spec import EvaluationSpec, load_evaluation_spec
from firewatch.ml.final_test_ledger import FinalTestLedger
from firewatch.ml.forest_artifact import PortableForest
from firewatch.ml.forest_package import dependencies as training_dependencies
from firewatch.ml.forest_package import read_forest_data, verify_forest
from firewatch.ml.forest_spec import ForestSpec
from firewatch.ml.release_evaluation import final_eligible, prepare_report, release_report
from firewatch.ml.split_package import _safe_file, _write
from firewatch.pipeline.handoff import _decode_json


def dependencies() -> dict[str, str]:
    return training_dependencies() | {"matplotlib": importlib.metadata.version("matplotlib")}


def _copy(source: Path, destination: Path) -> set[str]:
    manifest = _decode_json((source / "manifest.json").read_bytes())
    names = {"manifest.json", *manifest["files"]}
    for name in names:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_safe_file(source, name), target)
    return names


def _manifest(root: Path, stage: str, source_sha: str) -> None:
    _write(
        root / "manifest.json",
        dict(
            schema_version="release-evaluation-package-v1",
            stage=stage,
            source_manifest_sha256=source_sha,
            dependencies=dependencies(),
            final_test_status="opened_once" if stage == "final" else "sealed",
            files={
                p.relative_to(root).as_posix(): dict(
                    size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
                )
                for p in sorted(root.rglob("*"))
                if p.is_file() and p != root / "manifest.json"
            },
        ),
    )


def _stage(output: Path) -> tuple[Path, Path]:
    target = output.resolve()
    if target.exists():
        raise ValueError("Evaluation output exists; choose a new directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target, Path(
        tempfile.mkdtemp(prefix=".release-evaluation-", dir=target.parent)
    ).resolve()


def _cleanup(staging: Path, target: Path) -> None:
    if staging.exists():
        if staging.parent != target.parent or not staging.name.startswith(".release-evaluation-"):
            raise ValueError("Refusing cleanup outside owned evaluation staging directory")
        shutil.rmtree(staging)


def export_development(
    training: Path,
    output: Path,
    expected_hash: str,
    spec_path: Path = Path("configs/evaluation-v1.yaml"),
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("A trusted training manifest hash is required")
    verify_forest(training, expected_hash)
    spec = load_evaluation_spec(spec_path)
    data = read_forest_data(training / "split")
    model = _decode_json((training / "model.json").read_bytes())
    report, models = prepare_report(
        data,
        PortableForest.model_validate(model) if model is not None else None,
        ForestSpec.model_validate_json((training / "training-spec.json").read_bytes()),
        spec,
    )
    target, staging = _stage(output)
    try:
        _copy(training, staging / "training")
        _write(staging / "evaluation-spec.json", spec.model_dump(mode="json"))
        _write(staging / "evaluation-report.json", report)
        _write(
            staging / "evaluation-models.json", models.model_dump(mode="json") if models else None
        )
        write_model_card(staging, report)
        _manifest(staging, "development", expected_hash)
        verify_evaluation(staging)
        staging.rename(target)
        return report
    finally:
        _cleanup(staging, target)


def export_final(
    prepared: Path,
    output: Path,
    expected_hash: str,
    ledger_path: Path = Path("data/evaluations/final-test-ledger.sqlite3"),
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("A trusted frozen-development manifest hash is required")
    verified = verify_evaluation(prepared, expected_hash)
    if verified["stage"] != "development":
        raise ValueError("Final evaluation needs a frozen development artifact")
    development = _decode_json((prepared / "evaluation-report.json").read_bytes())
    data = read_forest_data(prepared / "training/split")
    spec = EvaluationSpec.model_validate_json((prepared / "evaluation-spec.json").read_bytes())
    if development["status"] != "development_frozen" or not final_eligible(data, spec):
        raise ValueError("Insufficient development/final holdout; final test remains sealed")
    models = EvaluationModels.model_validate_json(
        (prepared / "evaluation-models.json").read_bytes()
    )
    ledger = FinalTestLedger(ledger_path)
    key = development["holdout_key"]
    existing = ledger.get(key)
    if output.resolve().exists():
        if (
            existing is None
            or existing["prepared_sha256"] != expected_hash
            or existing["output_path"] != str(output.resolve())
        ):
            raise ValueError("Existing result does not match the one-shot final-test claim")
        result = verify_evaluation(output)
        outer = _decode_json((output / "manifest.json").read_bytes())
        if result["stage"] != "final" or outer["source_manifest_sha256"] != expected_hash:
            raise ValueError("Recovery result differs from frozen protocol")
        if existing["state"] != "complete":
            ledger.complete(key, expected_hash, output, result["manifest_sha256"])
        elif existing["result_sha256"] != result["manifest_sha256"]:
            raise ValueError("Committed final-test artifact checksum changed")
        saved = _decode_json((output / "evaluation-report.json").read_bytes())
        if not isinstance(saved, dict):
            raise ValueError("Malformed saved evaluation")
        return saved
    if existing is not None:
        raise ValueError(
            "Final holdout already claimed; failed attempts cannot rerun new experiments"
        )
    target, staging = _stage(output)
    claimed = False
    try:
        _copy(prepared, staging / "prepared")
        _write(staging / "evaluation-spec.json", spec.model_dump(mode="json"))
        claim = ledger.claim(key, expected_hash, target)
        claimed = True
        report = release_report(data, development, models, spec)
        _write(
            staging / "ledger-receipt.json",
            {k: claim[k] for k in ("holdout_key", "prepared_sha256", "claimed_at")},
        )
        _write(staging / "evaluation-report.json", report)
        write_model_card(staging, report)
        _manifest(staging, "final", expected_hash)
        result = verify_evaluation(staging)
        staging.rename(target)
        ledger.complete(key, expected_hash, target, result["manifest_sha256"])
        return report
    except BaseException:
        if claimed:
            ledger.fail(key)
        raise
    finally:
        _cleanup(staging, target)


def verify_evaluation(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    try:
        return _verify(root, expected_hash)
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("Malformed evaluation package") from None


def _verify(root: Path, expected_hash: str | None) -> dict[str, Any]:
    path = _safe_file(root, "manifest.json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Oversized evaluation manifest")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_hash is not None and (
        not re.fullmatch(r"[0-9a-f]{64}", expected_hash) or sha != expected_hash
    ):
        raise ValueError("Evaluation manifest checksum mismatch")
    manifest = _decode_json(path.read_bytes())
    stage = manifest["stage"]
    if (
        manifest["schema_version"] != "release-evaluation-package-v1"
        or stage not in {"development", "final"}
        or manifest["dependencies"] != dependencies()
        or manifest["final_test_status"] != ("sealed" if stage == "development" else "opened_once")
    ):
        raise ValueError("Evaluation contract/dependency mismatch")
    required = {"evaluation-spec.json", "evaluation-report.json", "model-card.md"}
    prefix = "training" if stage == "development" else "prepared"
    required |= {prefix + "/manifest.json"}
    required |= {"evaluation-models.json"} if stage == "development" else {"ledger-receipt.json"}
    if not required <= set(manifest["files"]):
        raise ValueError("Missing bound evaluation file")
    total = 0
    for name, entry in manifest["files"].items():
        file = _safe_file(root, name)
        total += file.stat().st_size
        if (
            file.stat().st_size > 64 * 1024 * 1024
            or total > 256 * 1024 * 1024
            or file.stat().st_size != entry["size"]
            or hashlib.sha256(file.read_bytes()).hexdigest() != entry["sha256"]
        ):
            raise ValueError("Evaluation file checksum/size mismatch")
    spec = EvaluationSpec.model_validate_json((root / "evaluation-spec.json").read_bytes())
    if stage == "development":
        verify_forest(root / "training", manifest["source_manifest_sha256"])
        data = read_forest_data(root / "training/split")
        saved_model = _decode_json((root / "training/model.json").read_bytes())
        report, models = prepare_report(
            data,
            PortableForest.model_validate(saved_model) if saved_model is not None else None,
            ForestSpec.model_validate_json((root / "training/training-spec.json").read_bytes()),
            spec,
        )
        if not _same(
            models.model_dump(mode="json") if models else None,
            _decode_json((root / "evaluation-models.json").read_bytes()),
        ):
            raise ValueError("Frozen evaluation models differ from development recomputation")
    else:
        child = _decode_json((root / "prepared/manifest.json").read_bytes())
        if child.get("stage") != "development":
            raise ValueError("Final source must be a development artifact")
        verify_evaluation(root / "prepared", manifest["source_manifest_sha256"])
        development = _decode_json((root / "prepared/evaluation-report.json").read_bytes())
        child_spec = EvaluationSpec.model_validate_json(
            (root / "prepared/evaluation-spec.json").read_bytes()
        )
        if spec != child_spec:
            raise ValueError("Evaluation policy changed after freeze")
        receipt = _decode_json((root / "ledger-receipt.json").read_bytes())
        if (
            receipt["holdout_key"] != development["holdout_key"]
            or receipt["prepared_sha256"] != manifest["source_manifest_sha256"]
        ):
            raise ValueError("Final-test receipt differs from frozen protocol")
        report = release_report(
            read_forest_data(root / "prepared/training/split"),
            development,
            EvaluationModels.model_validate_json(
                (root / "prepared/evaluation-models.json").read_bytes()
            ),
            spec,
        )
    if not _same(report, _decode_json((root / "evaluation-report.json").read_bytes())):
        raise ValueError("Evaluation report differs from frozen source-bound recomputation")
    nested = _decode_json((root / prefix / "manifest.json").read_bytes())
    names = required | {prefix + "/" + name for name in nested["files"]}
    with tempfile.TemporaryDirectory(prefix="firewatch-evaluation-render-") as folder:
        rendered = Path(folder)
        write_model_card(rendered, report)
        for file in rendered.iterdir():
            if file.read_bytes() != (root / file.name).read_bytes():
                raise ValueError("Model card/reliability plot differs from recomputation")
            names.add(file.name)
    if set(manifest["files"]) != names:
        raise ValueError("Evaluation inventory differs from frozen outputs")
    return dict(
        verified=True,
        manifest_sha256=sha,
        stage=stage,
        status=report.get("status", "release_evaluated"),
        final_test_status=report["final_test_status"],
        promotion_performed=False,
    )
