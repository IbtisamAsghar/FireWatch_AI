"""Portable Phase 4.1 label diagnostics; no training, splits, or network access."""

import hashlib
import shutil
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from firewatch.evidence.canonical import canonical_bytes
from firewatch.ml.labels import (
    DatasetDesign,
    FollowupEvidence,
    LabelResult,
    OutageCatalog,
    TargetInput,
    TargetSpec,
    label_snapshot,
    load_target_spec,
    training_cutoffs,
)
from firewatch.pipeline.handoff import _decode_json, _table, feature_schema, verify_handoff
from firewatch.processing.clusters import SnapshotBatch
from firewatch.processing.features import FEATURE_NAMES, MISSING_NAMES, MODEL_INPUT_NAMES, UNITS
from firewatch.repositories.labels import select_followup
from firewatch.repositories.revisions import RevisionPolicy
from firewatch.schemas.contracts import FeatureSet
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import utc


def load_design(path: Path) -> DatasetDesign:
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("Dataset design exceeds size bound")
    return DatasetDesign.model_validate(yaml.safe_load(raw))


def plan_design(design: DatasetDesign, spec: TargetSpec) -> dict[str, Any]:
    return dict(
        schema_version="target-cadence-plan-v1",
        target_spec_sha256=spec.sha256,
        periods=[
            dict(
                start=p.start.isoformat(),
                end=p.end.isoformat(),
                candidate_cutoffs=len(training_cutoffs(p.start, p.end, spec)),
            )
            for p in design.periods
        ],
        total_candidate_cutoffs=sum(
            len(training_cutoffs(p.start, p.end, spec)) for p in design.periods
        ),
        generated_feature_rows=0,
        training_ready=False,
    )


def _inputs(root: Path) -> list[TargetInput]:
    entries = _decode_json((root / "provenance.json").read_bytes())
    details = {
        (entry["run_id"], str(d.snapshot.snapshot_id)): d
        for entry in entries
        for d in SnapshotBatch.model_validate(
            entry["stage_manifests"]["clustering"]["batch"]
        ).snapshots
    }
    source_runs = {entry["run_id"]: entry for entry in entries}
    result = []
    for row in _table(root / "features.parquet", feature_schema(), 100000).to_pylist():
        feature = FeatureSet(
            run_id=row["run_id"],
            snapshot_id=row["snapshot_id"],
            mode=row["mode"],
            as_of=row["as_of"],
            feature_version=row["feature_version"],
            values={n: row[n] for n in FEATURE_NAMES},
            missing_flags={n: row[n + "_missing"] for n in MISSING_NAMES},
            units=UNITS,
            lineage=_decode_json(row["lineage_json"]),
            feature_hash=row["feature_hash"],
        )
        result.append(
            TargetInput(
                products=RevisionPolicy.model_validate(
                    source_runs[row["run_id"]]["stage_manifests"]["firms"]["policy"]
                ).products,
                region_name=source_runs[row["run_id"]]["region_name"],
                region_id=row["region_id"],
                feature=feature,
                detail=details[(row["run_id"], row["snapshot_id"])],
            )
        )
    return result


def label_report(rows: list[LabelResult], design: DatasetDesign) -> dict[str, Any]:
    eligible = [r for r in rows if r.label != "unknown" and not r.dataset_exclusions]
    reasons = Counter(reason for r in rows for reason in r.unknown_reasons)
    hours: Counter[str] = Counter()
    for row in rows:
        hours.update(row.acquisition_hours_utc)
    return dict(
        schema_version="target-label-report-v1",
        rows=len(rows),
        labels=dict(Counter(r.label for r in rows)),
        unknown_reasons=dict(sorted(reasons.items())),
        dataset_exclusions=dict(Counter(reason for r in rows for reason in r.dataset_exclusions)),
        on_cadence_known_label_rows=len(eligible),
        negatives_with_later_detections=sum(
            r.label == "negative" and r.unobserved_followup is False for r in rows
        ),
        negatives_without_later_detections=sum(
            r.label == "negative" and r.unobserved_followup is True for r in rows
        ),
        acquisition_hour_counts_utc=dict(sorted(hours.items())),
        hour_count_meaning="Detection counts across label windows; not independent overpass counts",
        sensor_product_policy="NOAA-20 SP only; NOAA-21 and NRT require separate cohorts",
        target_spec_sha256=design.target_spec_sha256,
        labels_are_observation_proxy=True,
        cloud_free_coverage_established=False,
        training_ready=False,
        splits_created=False,
        model_trained=False,
        pending=[
            "Seasonal feature materialization",
            "Reviewed event groups and class sufficiency",
            "Phase 4.2 chronological event-disjoint partitions and purge checks",
            "Negative-follow-up sensitivity and sensor/product evaluation at release",
        ],
    )


def _write(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value) + b"\n")


def export_labels(
    engine: Engine,
    handoff: Path,
    output: Path,
    *,
    handoff_sha256: str,
    spec_path: Path,
    design_path: Path,
    outages_path: Path,
    retrieval_cutoff: datetime | None = None,
) -> dict[str, Any]:
    """Verify saved features, select future archive separately, and atomically publish labels."""
    verified = verify_handoff(handoff, handoff_sha256)
    spec, design = load_target_spec(spec_path), load_design(design_path)
    raw = outages_path.read_bytes()
    if len(raw) > 256 * 1024:
        raise ValueError("Outage catalog exceeds size bound")
    outages = OutageCatalog.model_validate(yaml.safe_load(raw))
    available = utc(retrieval_cutoff or datetime.now(UTC))
    if available > datetime.now(UTC):
        raise ValueError("Retrieval cutoff cannot be in the future")
    items = _inputs(handoff)
    if len(items) > 10000:
        raise ValueError("Label package exceeds bounded snapshot count")
    return export_input_labels(
        engine,
        items,
        output,
        spec,
        design,
        outages,
        available,
        handoff_sha256=verified["manifest_sha256"],
    )


def export_input_labels(
    engine: Engine,
    items: list[TargetInput],
    output: Path,
    spec: TargetSpec,
    design: DatasetDesign,
    outages: OutageCatalog,
    available: datetime,
    *,
    handoff_sha256: str | None = None,
    backfill_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Shared label writer for verified handoffs and recomputed historical backfills."""
    if len(items) > 10000 or available > datetime.now(UTC):
        raise ValueError("Invalid label export count or availability cutoff")
    if (handoff_sha256 is None) == (backfill_source_sha256 is None):
        raise ValueError("Choose exactly one label provenance source")
    target = output.resolve()
    if target.exists():
        raise ValueError("Label output exists; choose a new directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".target-labels-", dir=target.parent)).resolve()
    if stage.parent != target.parent or not stage.name.startswith(".target-labels-"):
        raise ValueError("Invalid label staging path")
    try:
        evidence_cache = {}
        rows = []
        input_rows = []
        with Session(engine) as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            for item in items:
                key = (item.region_id, item.feature.as_of)
                if key not in evidence_cache:
                    evidence_cache[key] = select_followup(
                        session, item.region_id, item.feature.as_of, available, outages.outages
                    )
                evidence = evidence_cache[key]
                result = label_snapshot(item, evidence, spec)
                # Membership in the frozen study seasons is independent of target truth.
                if not any(p.start <= result.as_of < p.end for p in design.periods):
                    result.dataset_exclusions.append("outside_frozen_study_periods")
                if item.region_name != design.region:
                    result.dataset_exclusions.append("outside_frozen_study_region")
                elif evidence.bbox != design.bbox:
                    result.dataset_exclusions.append("source_bbox_differs_from_frozen_design")
                rows.append(result)
                name = "followup-" + result.followup_sha256 + ".json"
                if not (stage / name).exists():
                    _write(stage / name, evidence.model_dump(mode="json"))
                input_rows.append(dict(input=item.model_dump(mode="json"), followup_file=name))
        (stage / "inputs.jsonl").write_bytes(
            b"".join(canonical_bytes(row) + b"\n" for row in input_rows)
        )
        (stage / "labels.jsonl").write_bytes(
            b"".join(canonical_bytes(row.model_dump(mode="json")) + b"\n" for row in rows)
        )
        report = label_report(rows, design)
        _write(stage / "report.json", report)
        _write(stage / "target-spec.json", spec.model_dump(mode="json"))
        _write(stage / "dataset-design.json", design.model_dump(mode="json"))
        _write(
            stage / "manifest.json",
            dict(
                schema_version="target-label-package-v1",
                target_spec_sha256=spec.sha256,
                handoff_sha256=handoff_sha256,
                **(
                    {"backfill_source_sha256": backfill_source_sha256}
                    if backfill_source_sha256 is not None
                    else {}
                ),
                design_sha256=digest(design.model_dump(mode="json")),
                retrieval_cutoff=available.isoformat(),
                row_count=len(rows),
                model_input_names=list(MODEL_INPUT_NAMES),
                labels_in_feature_columns=False,
                training_ready=False,
                files={
                    p.name: dict(
                        sha256=hashlib.sha256(p.read_bytes()).hexdigest(), size=p.stat().st_size
                    )
                    for p in sorted(stage.iterdir())
                },
            ),
        )
        verify_label_package(stage)
        stage.rename(target)
        return report
    finally:
        if stage.exists():
            if stage.parent != target.parent or not stage.name.startswith(".target-labels-"):
                raise ValueError("Refusing label staging cleanup outside its owned directory")
            shutil.rmtree(stage)


def verify_label_package(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    try:
        return _verify_package(root, expected_hash)
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("Malformed target label package") from None


def _verify_package(root: Path, expected_hash: str | None) -> dict[str, Any]:
    path = root / "manifest.json"
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid target label manifest")
    manifest_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_hash and expected_hash != manifest_hash:
        raise ValueError("Label manifest checksum mismatch")
    manifest = _decode_json(path.read_bytes())
    required = {
        "inputs.jsonl",
        "labels.jsonl",
        "target-spec.json",
        "dataset-design.json",
        "report.json",
    }
    names = set(manifest["files"])
    if (
        manifest["schema_version"] != "target-label-package-v1"
        or not required <= names
        or manifest["training_ready"] is not False
        or manifest["labels_in_feature_columns"] is not False
        or manifest["model_input_names"] != list(MODEL_INPUT_NAMES)
    ):
        raise ValueError("Target label package contract mismatch")
    total_bytes = 0
    for name, item in manifest["files"].items():
        file = root / name
        if (
            Path(name).name != name
            or "\\" in name
            or ":" in name
            or name in {".", ".."}
            or file.is_symlink()
            or file.stat().st_size > 64 * 1024 * 1024
        ):
            raise ValueError("Unsafe label package file")
        total_bytes += file.stat().st_size
        if total_bytes > 256 * 1024 * 1024:
            raise ValueError("Label package total size bound exceeded")
        raw = file.read_bytes()
        if len(raw) != item["size"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("Label package file checksum mismatch")
    spec = TargetSpec.model_validate(_decode_json((root / "target-spec.json").read_bytes()))
    design = DatasetDesign.model_validate(_decode_json((root / "dataset-design.json").read_bytes()))
    if (
        spec.sha256 != manifest["target_spec_sha256"]
        or digest(design.model_dump(mode="json")) != manifest["design_sha256"]
    ):
        raise ValueError("Label target/design binding mismatch")
    inputs = [_decode_json(line) for line in (root / "inputs.jsonl").read_bytes().splitlines()]
    labels = [
        LabelResult.model_validate(_decode_json(line))
        for line in (root / "labels.jsonl").read_bytes().splitlines()
    ]
    if len(inputs) != len(labels) or len(labels) != manifest["row_count"] or len(labels) > 10000:
        raise ValueError("Label package row count mismatch")
    seen = set()
    used = set(required)
    for raw_input, saved in zip(inputs, labels, strict=True):
        item = TargetInput.model_validate(raw_input["input"])
        key = (item.feature.run_id, item.feature.snapshot_id)
        if key in seen:
            raise ValueError("Duplicate target snapshot")
        seen.add(key)
        name = raw_input["followup_file"]
        if name not in names or name != "followup-" + saved.followup_sha256 + ".json":
            raise ValueError("Label follow-up file binding mismatch")
        used.add(name)
        evidence = FollowupEvidence.model_validate(_decode_json((root / name).read_bytes()))
        if evidence.retrieval_cutoff.isoformat() != manifest["retrieval_cutoff"]:
            raise ValueError("Label availability binding mismatch")
        computed = label_snapshot(item, evidence, spec)
        if not any(p.start <= computed.as_of < p.end for p in design.periods):
            computed.dataset_exclusions.append("outside_frozen_study_periods")
        if item.region_name != design.region:
            computed.dataset_exclusions.append("outside_frozen_study_region")
        elif evidence.bbox != design.bbox:
            computed.dataset_exclusions.append("source_bbox_differs_from_frozen_design")
        if computed != saved:
            raise ValueError("Saved label differs from frozen recomputation")
    if names != used or _decode_json((root / "report.json").read_bytes()) != label_report(
        labels, design
    ):
        raise ValueError("Label report/inventory mismatch")
    return dict(
        verified=True, manifest_sha256=manifest_hash, rows=len(labels), training_ready=False
    )
