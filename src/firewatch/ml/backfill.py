"""Resumable, cache-only seasonal dataset preparation; no application DB writes."""

import hashlib
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.models import Region, Run
from firewatch.ml.dataset import export_input_labels, label_report, verify_label_package
from firewatch.ml.labels import (
    PRIMARY,
    DatasetDesign,
    LabelResult,
    OutageCatalog,
    TargetInput,
    TargetSpec,
    training_cutoffs,
)
from firewatch.ml.split_package import _safe_file, _write
from firewatch.pipeline.handoff import _decode_json
from firewatch.processing.clusters import ClusteringSpec, build_snapshots
from firewatch.processing.features import FeatureSpec, build_features
from firewatch.repositories.features import (
    EnrichmentPins,
    _enrichments,
    _request,
    _verify_pins,
    collect_enrichment,
)
from firewatch.repositories.revisions import RevisionPolicy, SelectedDetection, select_revisions
from firewatch.repositories.snapshots import policy_for_snapshots
from firewatch.schemas.common import RunMode
from firewatch.sources.firms_handoff import _observation
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import utc

SCHEMA = "historical-backfill-v1"


def cutoffs(design: DatasetDesign, start: datetime | None, end: datetime | None) -> list[datetime]:
    first, last = utc(start or design.periods[0].start), utc(end or design.periods[-1].end)
    if first >= last or first < design.periods[0].start or last > design.periods[-1].end:
        raise ValueError("Backfill range must stay inside the frozen study span")
    result = [
        t
        for p in design.periods
        for t in training_cutoffs(p.start, p.end, TargetSpec())
        if first <= t < last
    ]
    if not result:
        raise ValueError("Backfill range contains no frozen study cutoffs")
    return result


def _read(path: Path, limit: int = 64 * 1024 * 1024) -> Any:
    if path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("Unsafe or oversized backfill file")
    return _decode_json(path.read_bytes())


def _targets(proof: dict[str, Any], feature_spec: FeatureSpec) -> list[TargetInput]:
    if proof["status"] == "missing_lookback":
        if set(proof) != {"as_of", "status"}:
            raise ValueError("Invalid missing-source record")
        return []
    if proof["status"] != "prepared":
        raise ValueError("Unknown backfill cutoff state")
    selected = [SelectedDetection.model_validate(r) for r in proof["selected"]]
    for row in selected:
        _observation(row.observation, row.revision_hash, row.physical_key)
    policy = RevisionPolicy.model_validate(proof["policy"])
    pins = EnrichmentPins.model_validate(proof["pins"])
    if policy.products != PRIMARY or policy.retrieval_cutoff != pins.retrieval_cutoff:
        raise ValueError("Backfill source identity/availability mismatch")
    if set(policy.batch_ids) != {p.batch_id for p in pins.satellite_assets}:
        raise ValueError("Backfill satellite pins differ from source policy")
    if any(row.batch_id not in policy.batch_ids for row in selected):
        raise ValueError("Backfill selected revision is outside the pinned source")
    batch = build_snapshots(
        UUID(proof["run_id"]),
        RunMode.REPLAY,
        datetime.fromisoformat(proof["as_of"]),
        selected,
        ClusteringSpec(),
    )
    features = build_features(
        batch, selected, _enrichments(batch, pins), pins.satellite_assets, feature_spec
    )
    if digest(batch.model_dump(mode="json")) != proof["snapshot_sha256"]:
        raise ValueError("Backfill clustering differs from frozen recomputation")
    if {str(f.snapshot_id): f.feature_hash for f in features} != proof["feature_hashes"]:
        raise ValueError("Backfill features differ from frozen recomputation")
    details = {d.snapshot.snapshot_id: d for d in batch.snapshots}
    return [
        TargetInput(
            products=PRIMARY,
            region_name=proof["region"],
            region_id=proof["region_id"],
            detail=details[f.snapshot_id],
            feature=f,
        )
        for f in features
    ]


def _prepare(
    session: Session,
    area: Region,
    cutoff: datetime,
    available: datetime,
    config: FeatureSpec,
    job_hash: str,
) -> tuple[dict[str, Any], list[TargetInput], list[dict[str, Any]]]:
    try:
        policy = policy_for_snapshots(session, area.id, cutoff, PRIMARY, available)
    except ValueError as error:
        if "Missing complete source-day coverage" not in str(error):
            raise
        return dict(as_of=cutoff.isoformat(), status="missing_lookback"), [], []
    selected = select_revisions(
        session,
        area.id,
        cutoff - timedelta(hours=24),
        cutoff + timedelta(microseconds=1),
        policy,
    )
    identifier = uuid5(NAMESPACE_URL, f"{SCHEMA}:{job_hash}:{cutoff.isoformat()}")
    batch = build_snapshots(identifier, RunMode.REPLAY, cutoff, selected, ClusteringSpec())
    # A transient context supplies the shared enrichment builder; it is never added to a session.
    run = Run(id=identifier, region_id=area.id, mode=RunMode.REPLAY, as_of=cutoff)
    pins = collect_enrichment(session, run, batch, policy, config, available)
    _verify_pins(session, pins, config, cutoff.month)
    features = build_features(
        batch, selected, _enrichments(batch, pins), pins.satellite_assets, config
    )
    proof = dict(
        as_of=cutoff.isoformat(),
        status="prepared",
        run_id=str(identifier),
        region=area.name,
        region_id=str(area.id),
        policy=policy.model_dump(mode="json"),
        selected=[r.model_dump(mode="json") for r in selected],
        pins=pins.model_dump(mode="json"),
        snapshot_sha256=digest(batch.model_dump(mode="json")),
        feature_hashes={str(f.snapshot_id): f.feature_hash for f in features},
    )
    gaps: dict[str, dict[str, Any]] = {}
    for detail in batch.snapshots:
        request = _request(detail.snapshot, config)
        cell = request.cells[0]
        if {r.valid_at for r in pins.weather[cell.key].records} != set(request.hours()):
            gaps[cell.key] = request.model_dump(mode="json")
    return proof, _targets(proof, config), list(gaps.values())


def verify_chunk(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    manifest = _read(_safe_file(root, "manifest.json"), 1024 * 1024)
    sha = hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest()
    if expected_hash is not None and sha != expected_hash:
        raise ValueError("Backfill chunk manifest checksum mismatch")
    if manifest["schema_version"] != "historical-backfill-chunk-v1":
        raise ValueError("Unsupported backfill chunk")
    if set(manifest["files"]) != {"features.json", "weather-requests.json", "labels/manifest.json"}:
        raise ValueError("Backfill chunk inventory mismatch")
    for name, pin in manifest["files"].items():
        path = _safe_file(root, name)
        if path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("Oversized backfill input")
        raw = path.read_bytes()
        if len(raw) != pin["size"] or hashlib.sha256(raw).hexdigest() != pin["sha256"]:
            raise ValueError("Backfill chunk file checksum mismatch")
    source = _read(root / "features.json")
    if digest(source["job"]) != manifest["job_sha256"]:
        raise ValueError("Backfill job binding mismatch")
    config = FeatureSpec.model_validate(source["job"]["feature_spec"])
    available = datetime.fromisoformat(source["job"]["retrieval_cutoff"])
    expected_cutoffs = manifest["cutoffs"]
    if [p["as_of"] for p in source["cutoffs"]] != expected_cutoffs:
        raise ValueError("Backfill cutoff inventory mismatch")
    items = []
    for proof in source["cutoffs"]:
        if proof["status"] == "prepared":
            if EnrichmentPins.model_validate(proof["pins"]).retrieval_cutoff != available:
                raise ValueError("Backfill availability cutoff changed")
        items.extend(_targets(proof, config))
    verified = verify_label_package(
        root / "labels", manifest["files"]["labels/manifest.json"]["sha256"]
    )
    label_manifest = _read(root / "labels/manifest.json", 1024 * 1024)
    if label_manifest.get("backfill_source_sha256") != manifest["files"]["features.json"]["sha256"]:
        raise ValueError("Backfill labels do not reference the feature proof")
    saved = [
        _decode_json(line)["input"]
        for line in (root / "labels/inputs.jsonl").read_bytes().splitlines()
    ]
    if saved != [item.model_dump(mode="json") for item in items]:
        raise ValueError("Backfill labels differ from recomputed current inputs")
    labels = [
        LabelResult.model_validate_json(line)
        for line in (root / "labels/labels.jsonl").read_bytes().splitlines()
    ]
    summary = dict(
        manifest_sha256=sha,
        cutoffs=len(source["cutoffs"]),
        missing_lookback=sum(p["status"] == "missing_lookback" for p in source["cutoffs"]),
        rows=verified["rows"],
        labels=dict(Counter(row.label for row in labels)),
        eligible_rows=sum(row.label != "unknown" and not row.dataset_exclusions for row in labels),
        eligible_labels=dict(
            Counter(
                row.label for row in labels if row.label != "unknown" and not row.dataset_exclusions
            )
        ),
        missing_weather_rows=sum(item.feature.missing_flags["temperature_c"] for item in items),
        weather_requests=len(_read(root / "weather-requests.json")),
    )
    return summary


def run_backfill(
    engine: Engine,
    settings: Settings,
    output: Path,
    design: DatasetDesign,
    config: FeatureSpec,
    outages: OutageCatalog,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    retrieval_cutoff: datetime | None = None,
    chunk_size: int = 16,
    resume: bool = False,
    progress: Callable[[dict[str, Any]], None] = lambda _: None,
) -> dict[str, Any]:
    if not 1 <= chunk_size <= 32:
        raise ValueError("Use 1-32 cutoffs per backfill chunk")
    times = cutoffs(design, start, end)
    root = output.resolve()
    if root.is_symlink():
        raise ValueError("Backfill output cannot be a symlink")
    previous = None
    if root.exists():
        if not resume:
            raise ValueError("Backfill output exists; use explicit --resume")
        previous = _read(root / "job.json", 1024 * 1024)
    available = utc(
        retrieval_cutoff
        or (datetime.fromisoformat(previous["retrieval_cutoff"]) if previous else datetime.now(UTC))
    )
    if available > datetime.now(UTC) or any(t + timedelta(hours=24) > available for t in times):
        raise ValueError("Backfill requires elapsed follow-up and a nonfuture retrieval cutoff")
    job = dict(
        schema_version=SCHEMA,
        design=design.model_dump(mode="json"),
        feature_spec=config.model_dump(mode="json"),
        outages=outages.model_dump(mode="json"),
        retrieval_cutoff=available.isoformat(),
        cutoffs=[t.isoformat() for t in times],
        chunk_size=chunk_size,
        source_policy="NOAA20_SP_only",
    )
    if previous is not None and previous != job:
        raise ValueError("Resume configuration changed; create a new output directory")
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "job.json", job)
    job_hash = digest(job)
    chunks = []
    for index, offset in enumerate(range(0, len(times), chunk_size)):
        group = times[offset : offset + chunk_size]
        target = root / f"chunk-{index:04d}"
        if target.exists():
            manifest = _read(target / "manifest.json", 1024 * 1024)
            if manifest["job_sha256"] != job_hash or manifest["cutoffs"] != [
                t.isoformat() for t in group
            ]:
                raise ValueError("Existing backfill chunk belongs to another job")
            result = verify_chunk(target)
        else:
            stage = Path(tempfile.mkdtemp(prefix=".backfill-", dir=root)).resolve()
            try:
                proofs, items, requests = [], [], []
                with Session(engine) as session, session.begin():
                    session.execute(
                        text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                    )
                    area = session.scalar(select(Region).where(Region.name == design.region))
                    if area is None or (area.west, area.south, area.east, area.north) != (
                        design.bbox.west,
                        design.bbox.south,
                        design.bbox.east,
                        design.bbox.north,
                    ):
                        raise ValueError("Backfill region/bbox does not match the frozen design")
                    for cutoff in group:
                        proof, rows, missing = _prepare(
                            session, area, cutoff, available, config, job_hash
                        )
                        proofs.append(proof)
                        items.extend(rows)
                        requests.extend(missing)
                _write(stage / "features.json", dict(job=job, cutoffs=proofs))
                _write(stage / "weather-requests.json", requests)
                proof_sha = hashlib.sha256((stage / "features.json").read_bytes()).hexdigest()
                export_input_labels(
                    engine,
                    items,
                    stage / "labels",
                    TargetSpec(),
                    design,
                    outages,
                    available,
                    backfill_source_sha256=proof_sha,
                )
                _write(
                    stage / "manifest.json",
                    dict(
                        schema_version="historical-backfill-chunk-v1",
                        job_sha256=job_hash,
                        cutoffs=[t.isoformat() for t in group],
                        files={
                            name: dict(
                                size=(stage / name).stat().st_size,
                                sha256=hashlib.sha256((stage / name).read_bytes()).hexdigest(),
                            )
                            for name in (
                                "features.json",
                                "weather-requests.json",
                                "labels/manifest.json",
                            )
                        },
                    ),
                )
                result = verify_chunk(stage)
                stage.rename(target)
            finally:
                if stage.exists():
                    if stage.parent != root or not stage.name.startswith(".backfill-"):
                        raise ValueError("Refusing cleanup outside owned backfill staging")
                    shutil.rmtree(stage)
        chunks.append(dict(path=target.name, **result))
        progress(
            dict(
                completed_chunks=len(chunks),
                total_chunks=(len(times) + chunk_size - 1) // chunk_size,
                **result,
            )
        )
        _write_summary(root, job_hash, chunks, len(times))
    return _write_summary(root, job_hash, chunks, len(times))


def _write_summary(
    root: Path, job_hash: str, chunks: list[dict[str, Any]], planned: int
) -> dict[str, Any]:
    labels: Counter[str] = Counter()
    eligible: Counter[str] = Counter()
    for chunk in chunks:
        labels.update(chunk["labels"])
        eligible.update(chunk["eligible_labels"])
    summary = dict(
        schema_version="historical-backfill-summary-v1",
        job_sha256=job_hash,
        planned_cutoffs=planned,
        processed_cutoffs=sum(c["cutoffs"] for c in chunks),
        missing_lookback_cutoffs=sum(c["missing_lookback"] for c in chunks),
        rows=sum(c["rows"] for c in chunks),
        labels=dict(labels),
        eligible_rows=sum(c["eligible_rows"] for c in chunks),
        eligible_labels=dict(eligible),
        missing_weather_rows=sum(c["missing_weather_rows"] for c in chunks),
        weather_requests=sum(c["weather_requests"] for c in chunks),
        chunks=chunks,
        application_database_writes=0,
        model_trained=False,
        model_approved=False,
        active_scorer_changed=False,
        final_test_evaluated=False,
        final_test_status="sealed",
    )
    _write(root / ".summary-next.json", summary)
    (root / ".summary-next.json").replace(root / "summary.json")
    return summary


def merge_backfill_labels(root: Path, output: Path) -> dict[str, Any]:
    """Join verified chunks into the existing bounded label format for event splitting."""
    job = _read(root / "job.json", 1024 * 1024)
    job_hash = digest(job)
    groups = [
        job["cutoffs"][i : i + job["chunk_size"]]
        for i in range(0, len(job["cutoffs"]), job["chunk_size"])
    ]
    target = output.resolve()
    if target.exists() or target.is_relative_to(root.resolve()):
        raise ValueError("Use a new label directory outside the backfill job")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".target-labels-", dir=target.parent)).resolve()
    try:
        input_bytes, label_bytes, labels = [], [], []
        for index, group in enumerate(groups):
            chunk = root / f"chunk-{index:04d}"
            manifest = _read(chunk / "manifest.json", 1024 * 1024)
            if manifest["job_sha256"] != job_hash or manifest["cutoffs"] != group:
                raise ValueError("Incomplete or mismatched backfill collection")
            verify_chunk(chunk)
            source = chunk / "labels"
            input_bytes.extend((source / "inputs.jsonl").read_bytes().splitlines(keepends=True))
            label_bytes.extend((source / "labels.jsonl").read_bytes().splitlines(keepends=True))
            if len(input_bytes) > 10000:
                raise ValueError(
                    "Merged label dataset exceeds 10000-row contract; retain verified shards"
                )
            child = _read(source / "manifest.json", 1024 * 1024)
            for name in child["files"]:
                if not name.startswith("followup-"):
                    continue
                raw = _safe_file(source, name).read_bytes()
                destination = stage / name
                if destination.exists() and destination.read_bytes() != raw:
                    raise ValueError("Conflicting follow-up evidence across chunks")
                destination.write_bytes(raw)
        (stage / "inputs.jsonl").write_bytes(b"".join(input_bytes))
        (stage / "labels.jsonl").write_bytes(b"".join(label_bytes))
        labels = [LabelResult.model_validate_json(line) for line in label_bytes]
        design = DatasetDesign.model_validate(job["design"])
        _write(stage / "target-spec.json", TargetSpec().model_dump(mode="json"))
        _write(stage / "dataset-design.json", job["design"])
        _write(stage / "report.json", label_report(labels, design))
        # Existing schema allows an explicitly different, separately verified provenance source.
        from firewatch.processing.features import MODEL_INPUT_NAMES

        _write(
            stage / "manifest.json",
            dict(
                schema_version="target-label-package-v1",
                target_spec_sha256=TargetSpec().sha256,
                handoff_sha256=None,
                backfill_source_sha256=job_hash,
                design_sha256=digest(job["design"]),
                retrieval_cutoff=job["retrieval_cutoff"],
                row_count=len(labels),
                model_input_names=list(MODEL_INPUT_NAMES),
                labels_in_feature_columns=False,
                training_ready=False,
                files={
                    p.name: dict(
                        size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
                    )
                    for p in sorted(stage.iterdir())
                },
            ),
        )
        verified = verify_label_package(stage)
        stage.rename(target)
        return verified
    finally:
        if stage.exists():
            if stage.parent != target.parent or not stage.name.startswith(".target-labels-"):
                raise ValueError("Refusing cleanup outside owned label staging")
            shutil.rmtree(stage)
