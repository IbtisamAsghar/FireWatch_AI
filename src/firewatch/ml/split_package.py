"""Offline, source-bound event catalogs and reproducible split packages."""

import hashlib
import shutil
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from firewatch.evidence.canonical import canonical_bytes
from firewatch.ml.dataset import verify_label_package
from firewatch.ml.events import (
    EventCatalog,
    EventObservation,
    EventReview,
    EventSpec,
    build_event_catalog,
)
from firewatch.ml.labels import DatasetDesign
from firewatch.ml.splits import SplitResult, SplitSample, SplitSpec, create_splits
from firewatch.pipeline.handoff import _decode_json
from firewatch.processing.features import MODEL_INPUT_NAMES
from firewatch.schemas.common import Product, Sensor
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.archive_manifest import ArchiveManifest
from firewatch.sources.firms_handoff import _observation
from firewatch.sources.firms_normalize import RowRevision


def _safe_file(root: Path, name: str) -> Path:
    parts = PurePosixPath(name)
    if (
        parts.is_absolute()
        or ".." in parts.parts
        or "\\" in name
        or ":" in name
        or str(parts) != name
    ):
        raise ValueError("Unsafe split package path")
    file = root.joinpath(*parts.parts)
    if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
        raise ValueError("Split package file escapes root")
    return file


def load_event_spec(path: Path) -> EventSpec:
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("Oversized event specification")
    return EventSpec.model_validate(yaml.safe_load(raw))


def load_split_spec(path: Path) -> SplitSpec:
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("Oversized split specification")
    return SplitSpec.model_validate(yaml.safe_load(raw))


def load_source_observations(root: Path, expected_hash: str) -> list[EventObservation]:
    path = _safe_file(root, "manifest.json")
    if (
        path.stat().st_size > 1024 * 1024
        or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash
    ):
        raise ValueError("Source event manifest checksum mismatch")
    manifest = _decode_json(path.read_bytes())
    if "archive-revisions.jsonl" not in manifest["files_sha256"]:
        raise ValueError("Missing bound seasonal revision source")
    total = 0
    for name, checksum in manifest["files_sha256"].items():
        file = _safe_file(root, name)
        total += file.stat().st_size
        if (
            file.stat().st_size > 64 * 1024 * 1024
            or total > 256 * 1024 * 1024
            or hashlib.sha256(file.read_bytes()).hexdigest() != checksum
        ):
            raise ValueError("Event source file checksum/size mismatch")
    batches = {}
    imports = {}
    counts: Counter[str] = Counter()
    identities: dict[str, set[str]] = {}
    for dataset in manifest["datasets"]:
        source = ArchiveManifest.model_validate(dataset["source_manifest"])
        if (
            source.product != Product.NOAA20_SP
            or source.sensor != Sensor.NOAA20
            or source.region != "northern-california"
            or source.bbox != BoundingBox(west=-122.5, south=38.5, east=-120.0, north=40.5)
        ):
            raise ValueError("Event catalog requires the frozen primary SP sensor/product")
        if dataset["import_id"] in imports:
            raise ValueError("Duplicate seasonal import identity")
        imports[dataset["import_id"]] = dataset
        identities[dataset["import_id"]] = set()
        if {b["date"] for b in dataset["source_batches"]} != {str(day) for day in source.days()}:
            raise ValueError("Seasonal batch inventory does not match declared interval")
        for batch in dataset["source_batches"]:
            key = (source.product, batch["date"])
            if (
                key in batches
                or batch["coverage_complete"] is not True
                or not source.covered(date.fromisoformat(batch["date"]))
            ):
                raise ValueError("Overlapping or incomplete seasonal event sources")
            batches[key] = (dataset["import_id"], batch["batch_id"])
    for year in (2022, 2024):
        first, last = date(year, 4, 30), date(year, 11, 1)
        if any(
            (Product.NOAA20_SP, str(first + timedelta(days=i))) not in batches
            for i in range((last - first).days + 1)
        ):
            raise ValueError("Full seasonal source-day inventory required for whole-event grouping")
    observations: dict[str, EventObservation] = {}
    with _safe_file(root, "archive-revisions.jsonl").open("rb") as stream:
        for count, line in enumerate(stream, start=1):
            if count > 100000 or len(line) > 256 * 1024:
                raise ValueError("Event source row bound exceeded")
            row = _decode_json(line)
            rev = RowRevision.model_validate(row["revision"])
            obs = rev.observation
            _observation(obs, rev.revision_hash, rev.physical_key)
            if (
                obs.product != Product.NOAA20_SP
                or obs.sensor != Sensor.NOAA20
                or row["coverage_complete"] is not True
                or batches.get((obs.product, str(obs.acquired_at.date())))
                != (row["import_id"], row["batch_id"])
            ):
                raise ValueError("Event observation source binding mismatch")
            counts[row["import_id"]] += 1
            identities[row["import_id"]].add(obs.dedup_key)
            physical = rev.physical_key
            if physical in observations:
                saved = observations[physical]
                if (saved.acquired_at, saved.latitude, saved.longitude) != (
                    obs.acquired_at,
                    obs.latitude,
                    obs.longitude,
                ):
                    raise ValueError("Conflicting physical observation identity")
                if rev.revision_hash not in saved.revision_hashes:
                    saved.revision_hashes.append(rev.revision_hash)
            else:
                observations[physical] = EventObservation(
                    physical_key=physical,
                    acquired_at=obs.acquired_at,
                    latitude=obs.latitude,
                    longitude=obs.longitude,
                    revision_hashes=[rev.revision_hash],
                )
    if any(
        counts[key] != data["quality"]["revision_count"]
        or len(identities[key]) != data["quality"]["accepted_count"]
        for key, data in imports.items()
    ):
        raise ValueError("Seasonal event revision inventory counts disagree")
    return sorted(observations.values(), key=lambda r: (r.acquired_at, r.physical_key))


def inspection(catalog: EventCatalog) -> dict[str, Any]:
    return dict(
        schema_version="partition-event-inspection-v1",
        catalog_sha256=catalog.sha256,
        observations=len(catalog.observations),
        groups=len(catalog.groups),
        flags=dict(Counter(flag for g in catalog.groups for flag in g.review_flags)),
        near_miss_pairs=len(catalog.near_misses),
        near_misses_truncated=catalog.near_misses_truncated,
        largest_groups=[
            g.model_dump(mode="json")
            | {"physical_keys": None, "observations": len(g.physical_keys)}
            for g in sorted(catalog.groups, key=lambda g: (-len(g.physical_keys), g.event_id))[:10]
        ],
        grouping_is_incident_ground_truth=False,
    )


def read_samples(root: Path) -> list[SplitSample]:
    inputs = [_decode_json(line) for line in (root / "inputs.jsonl").read_bytes().splitlines()]
    labels = [_decode_json(line) for line in (root / "labels.jsonl").read_bytes().splitlines()]
    return [
        SplitSample(target=item["input"], label=label)
        for item, label in zip(inputs, labels, strict=True)
    ]


def _write(path: Path, value: Any) -> None:
    path.write_bytes(canonical_bytes(value) + b"\n")


def export_splits(
    labels: Path,
    output: Path,
    *,
    label_sha256: str,
    sources: Path,
    source_sha256: str,
    spec_path: Path,
    review_path: Path | None = None,
    event_spec_path: Path = Path("configs/event-groups-v1.yaml"),
) -> SplitResult:
    verify_label_package(labels, label_sha256)
    spec = load_split_spec(spec_path)
    catalog = build_event_catalog(
        load_source_observations(sources, source_sha256),
        source_sha256,
        load_event_spec(event_spec_path),
    )
    review = (
        EventReview.model_validate(_decode_json(review_path.read_bytes())) if review_path else None
    )
    samples = read_samples(labels)
    result = create_splits(samples, catalog, spec, review)
    target = output.resolve()
    if target.exists():
        raise ValueError("Split output exists; choose a new directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".event-splits-", dir=target.parent)).resolve()
    if staging.parent != target.parent or not staging.name.startswith(".event-splits-"):
        raise ValueError("Invalid split staging path")
    try:
        (staging / "labels").mkdir()
        label_manifest = _decode_json((labels / "manifest.json").read_bytes())
        for name in ["manifest.json", *label_manifest["files"]]:
            shutil.copyfile(_safe_file(labels, name), staging / "labels" / name)
        for name, value in [
            ("event-catalog.json", catalog.model_dump(mode="json")),
            ("event-review.json", review.model_dump(mode="json") if review else None),
            ("split-spec.json", spec.model_dump(mode="json")),
            ("membership.json", result.model_dump(mode="json")),
            ("inspection.json", inspection(catalog)),
        ]:
            _write(staging / name, value)
        _write(
            staging / "manifest.json",
            dict(
                schema_version="event-split-package-v1",
                label_package_sha256=label_sha256,
                source_manifest_sha256=source_sha256,
                catalog_sha256=catalog.sha256,
                dataset_sha256=result.dataset_sha256,
                spec_sha256=spec.sha256,
                model_input_names=list(MODEL_INPUT_NAMES),
                event_ids_are_features=False,
                final_test_status="sealed",
                training_ready=result.training_ready,
                files={
                    p.relative_to(staging).as_posix(): dict(
                        size=p.stat().st_size, sha256=hashlib.sha256(p.read_bytes()).hexdigest()
                    )
                    for p in sorted(staging.rglob("*"))
                    if p.is_file()
                },
            ),
        )
        verify_split_package(staging)
        staging.rename(target)
        return result
    finally:
        if staging.exists():
            if staging.parent != target.parent or not staging.name.startswith(".event-splits-"):
                raise ValueError("Refusing cleanup outside owned split staging directory")
            shutil.rmtree(staging)


def verify_split_package(root: Path, expected_hash: str | None = None) -> dict[str, Any]:
    try:
        return _verify(root, expected_hash)
    except (KeyError, TypeError, AttributeError, RecursionError):
        raise ValueError("Malformed split package") from None


def _verify(root: Path, expected_hash: str | None) -> dict[str, Any]:
    path = _safe_file(root, "manifest.json")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Oversized split manifest")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_hash and sha != expected_hash:
        raise ValueError("Split manifest checksum mismatch")
    manifest = _decode_json(path.read_bytes())
    if (
        manifest["schema_version"] != "event-split-package-v1"
        or manifest["model_input_names"] != list(MODEL_INPUT_NAMES)
        or manifest["event_ids_are_features"] is not False
        or manifest["final_test_status"] != "sealed"
    ):
        raise ValueError("Split manifest contract mismatch")
    required = {
        "event-catalog.json",
        "event-review.json",
        "split-spec.json",
        "membership.json",
        "inspection.json",
        "labels/manifest.json",
    }
    if not required <= set(manifest["files"]):
        raise ValueError("Missing bound split package file")
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
            raise ValueError("Split file checksum/size mismatch")
    verify_label_package(root / "labels", manifest["label_package_sha256"])
    catalog = EventCatalog.model_validate(_decode_json((root / "event-catalog.json").read_bytes()))
    if (
        build_event_catalog(catalog.observations, catalog.source_manifest_sha256, catalog.spec)
        != catalog
    ):
        raise ValueError("Event catalog differs from deterministic recomputation")
    raw_review = _decode_json((root / "event-review.json").read_bytes())
    review = EventReview.model_validate(raw_review) if raw_review is not None else None
    spec = SplitSpec.model_validate(_decode_json((root / "split-spec.json").read_bytes()))
    result = create_splits(read_samples(root / "labels"), catalog, spec, review)
    expected = SplitResult.model_validate(_decode_json((root / "membership.json").read_bytes()))
    label_manifest = _decode_json((root / "labels" / "manifest.json").read_bytes())
    design = DatasetDesign.model_validate(
        _decode_json((root / "labels" / "dataset-design.json").read_bytes())
    )
    names = {
        "event-catalog.json",
        "event-review.json",
        "split-spec.json",
        "membership.json",
        "inspection.json",
        "labels/manifest.json",
    } | {"labels/" + n for n in label_manifest["files"]}
    if (
        set(manifest["files"]) != names
        or result != expected
        or result.dataset_sha256 != manifest["dataset_sha256"]
        or result.spec_sha256 != manifest["spec_sha256"]
        or catalog.sha256 != manifest["catalog_sha256"]
        or catalog.source_manifest_sha256 != manifest["source_manifest_sha256"]
        or result.training_ready != manifest["training_ready"]
        or design.target_spec_sha256 != spec.target_spec_sha256
        or _decode_json((root / "inspection.json").read_bytes()) != inspection(catalog)
    ):
        raise ValueError("Split dataset/partition/freeze binding mismatch")
    return dict(
        verified=True,
        manifest_sha256=sha,
        rows=len(result.membership),
        events=len(catalog.groups),
        strategy=result.strategy,
        training_ready=result.training_ready,
        final_test_status="sealed",
    )
