"""Offline real Phase 4.1 receipt and sensor/product/overpass diagnostics."""

import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any

from firewatch.ml.dataset import verify_label_package
from firewatch.pipeline.handoff import _decode_json
from firewatch.processing.clusters import distance_km
from firewatch.repositories.revisions import SelectedDetection
from firewatch.sources.firms_handoff import _observation
from firewatch.sources.firms_normalize import RowRevision

SOURCE_HASH = "7b2c87453b24e5669297128232d30e8f2ca6a76a5fe3efd65452c5d2f4eafe0f"


def profile_sources(root: Path, expected_hash: str) -> dict[str, Any]:
    raw = (root / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise ValueError("Reviewed source handoff manifest changed")
    manifest = _decode_json(raw)
    for name, checksum in manifest["files_sha256"].items():
        path = root / name
        if (
            Path(name).name != name
            or "\\" in name
            or ":" in name
            or path.is_symlink()
            or path.stat().st_size > 64 * 1024 * 1024
            or hashlib.sha256(path.read_bytes()).hexdigest() != checksum
        ):
            raise ValueError("Source profile checksum/path check failed")
    selected = {}
    for path in sorted(root.glob("*.jsonl")):
        if path.name not in manifest["files_sha256"]:
            raise ValueError("Unbound source profile input")
        with path.open("rb") as stream:
            for count, line in enumerate(stream, start=1):
                if count > 100000 or len(line) > 256 * 1024:
                    raise ValueError("Source profile row bound exceeded")
                row = _decode_json(line)
                if "revision" in row:
                    revision = RowRevision.model_validate(row["revision"])
                    obs, revision_hash = revision.observation, revision.revision_hash
                    _observation(obs, revision_hash, revision.physical_key)
                    identity = (row["import_id"], row["batch_id"], obs.dedup_key)
                else:
                    detection = SelectedDetection.model_validate(row)
                    obs, revision_hash = detection.observation, detection.revision_hash
                    _observation(obs, revision_hash, detection.physical_key)
                    identity = (path.name, str(detection.batch_id), obs.dedup_key)
                compact = dict(
                    sensor=obs.sensor.value,
                    product=obs.product.value,
                    year=obs.acquired_at.year,
                    acquired_at=obs.acquired_at,
                    latitude=obs.latitude,
                    longitude=obs.longitude,
                    frp_mw=obs.frp_mw,
                    confidence=obs.confidence.value,
                    daynight=obs.daynight.value if obs.daynight else None,
                )
                if identity not in selected or revision_hash > selected[identity][0]:
                    selected[identity] = (revision_hash, compact)
    cohorts = {}
    nrt = []
    for _, row in selected.values():
        key = (row["sensor"], row["product"], row["year"])
        cohorts.setdefault(key, []).append(row)
        if row["product"].endswith("_NRT"):
            nrt.append(row)
    summaries = []
    for (sensor, product, year), rows in sorted(cohorts.items()):
        frp = [r["frp_mw"] for r in rows if r["frp_mw"] is not None]
        summaries.append(
            dict(
                sensor=sensor,
                product=product,
                year=year,
                observations=len(rows),
                frp_mw=dict(
                    valid=len(frp),
                    mean=mean(frp) if frp else None,
                    median=median(frp) if frp else None,
                    maximum=max(frp) if frp else None,
                ),
                confidence=dict(Counter(r["confidence"] for r in rows)),
                daynight=dict(Counter(r["daynight"] or "unknown" for r in rows)),
                acquisition_hours_utc=dict(
                    sorted(Counter(f"{r['acquired_at'].hour:02d}" for r in rows).items())
                ),
                distinct_positive_acquisition_times=len({r["acquired_at"] for r in rows}),
            )
        )
    if len(nrt) > 1000:
        raise ValueError("Pairwise comparison bound exceeded")
    a, b = [[r for r in nrt if r["sensor"] == sensor] for sensor in ("NOAA-20", "NOAA-21")]
    matches = [
        (i, j)
        for i, left in enumerate(a)
        for j, right in enumerate(b)
        if abs((left["acquired_at"] - right["acquired_at"]).total_seconds()) <= 1800
        and distance_km(left["latitude"], left["longitude"], right["latitude"], right["longitude"])
        <= 2
    ]
    return dict(
        source_manifest_sha256=expected_hash,
        cohorts=summaries,
        nrt_comparison=dict(
            radius_km=2,
            time_tolerance_minutes=30,
            noaa20_observations=len(a),
            noaa21_observations=len(b),
            noaa20_with_nearby_noaa21=len({i for i, _ in matches}),
            noaa21_with_nearby_noaa20=len({j for _, j in matches}),
        ),
        pooling_approved=False,
        limitations=[
            "Counts select one revision per source identity; they are not fire-event counts.",
            "Positive acquisition times and hourly histograms are not complete "
            "overpass inventories.",
            "NRT overlap is descriptive and is not an SP/NRT equivalence or calibration test.",
            "NOAA-21 SP seasonal comparison is unavailable in these imported inputs.",
            "Raw cohort differences confound time, region and observation conditions.",
        ],
    )


def main() -> None:
    root = Path("data/labels/phase-4.1-final")
    verified = verify_label_package(root)
    profile = profile_sources(Path("data/member-4/phase-2.6-final-handoff"), SOURCE_HASH)
    receipt = dict(
        schema_version="phase-4.1-smoke-v1",
        data_kind="real-cached-historical",
        label_package=verified,
        report=_decode_json((root / "report.json").read_bytes()),
        sensor_product_diagnostics=profile,
        provider_requests=0,
        application_database_writes=0,
        saved_features_unchanged=True,
        labels_are_retrospective=True,
        model_trained=False,
        splits_created=False,
    )
    Path("docs/phase-4.1-smoke.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                label_rows=verified["rows"],
                verified=verified["verified"],
                source_cohorts=len(profile["cohorts"]),
                training_ready=False,
            )
        )
    )


if __name__ == "__main__":
    main()
