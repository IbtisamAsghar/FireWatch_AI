"""Offline, fail-closed verification and export of completed FIRMS source handoffs."""

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from firewatch.repositories.revisions import SelectedDetection
from firewatch.schemas.contracts import DetectionInput
from firewatch.schemas.firms_refresh import RefreshState, RefreshSummary, summarize_refresh
from firewatch.sources.archive_manifest import ArchiveManifest
from firewatch.sources.archive_stage import atomic_json, hash_file
from firewatch.sources.firms_normalize import (
    KEY_VERSION,
    NORMALIZER_VERSION,
    RowRevision,
    digest,
)

UNITS = {"frp_mw": "MW", "brightness_k": "K", "bright_ti5_k": "K", "scan": "km", "track": "km"}


class HandoffError(ValueError):
    """Fixed public message; input/provider contents are never rendered."""


def scan_secrets(paths: Iterable[Path], secrets: Iterable[str]) -> int:
    """Scan bounded chunks, including secrets crossing chunk boundaries. Never echo matches."""
    needles = [value.encode() for value in secrets if value]
    overlap = max((len(value) for value in needles), default=1) - 1
    count = 0
    for path in paths:
        tail = b""
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                content = tail + chunk
                if any(value in content for value in needles):
                    raise HandoffError("Secret material detected; handoff publication stopped.")
                tail = content[-overlap:] if overlap else b""
        count += 1
    return count


def _json(path: Path) -> dict[str, Any]:
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise HandoffError("Invalid handoff document.")
    return result


def _observation(value: DetectionInput, revision_hash: str, physical_key: str) -> None:
    metadata = value.raw_values.get("normalization")
    if (
        digest(value.model_dump(mode="json")) != revision_hash
        or not isinstance(metadata, dict)
        or metadata.get("physical_key") != physical_key
        or metadata.get("units") != UNITS
        or metadata.get("version") != NORMALIZER_VERSION
        or metadata.get("key_version") != KEY_VERSION
    ):
        raise HandoffError("Normalized revision integrity or unit check failed.")


def export_firms_handoff(
    archives: list[Path], refreshes: list[Path], output: Path, *, secrets: Iterable[str] = ()
) -> dict[str, Any]:
    """Export revision snapshots, not labels or frozen pipeline inputs; never contact NASA/DB.

    Output must be new. Only the final manifest marks a successful export; failed temporary
    folders can be inspected or removed by the caller. Archive SQLite is opened read-only.
    """
    try:
        return _export(archives, refreshes, output, list(secrets))
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        if isinstance(error, HandoffError):
            raise
        raise HandoffError(
            "FIRMS handoff validation failed; no completed manifest published."
        ) from None


def _export(
    archives: list[Path], refreshes: list[Path], output: Path, secrets: list[str]
) -> dict[str, Any]:
    if not archives:
        raise HandoffError("At least one completed historical archive is required.")
    if output.exists():
        raise HandoffError("Handoff output must be a new directory.")
    output.mkdir(parents=True)
    temporary = output / ("revisions.tmp-" + uuid4().hex)
    datasets: list[dict[str, Any]] = []
    seen: set[str] = set()
    counts = {"fetched": 0, "accepted": 0, "rejected": 0, "duplicates": 0, "revisions": 0}
    with temporary.open("wb") as stream:
        for path in archives:
            handoff = _json(path)
            manifest = ArchiveManifest.model_validate(handoff["source_manifest"])
            identity = str(UUID(handoff["import_id"]))
            if (
                identity in seen
                or handoff["status"] != "completed"
                or handoff["schema_version"] != "firms-archive-import-v1"
            ):
                raise HandoffError("Archive is repeated or not completed.")
            seen.add(identity)
            original = Path(handoff["original_path"])
            quality_path = Path(handoff["quality_path"])
            quality = _json(quality_path)
            index = path.parent / "index.sqlite"
            scan_secrets([path, original, quality_path, index], secrets)
            if (
                hash_file(original) != manifest.sha256
                or quality["source_sha256"] != manifest.sha256
                or quality["normalizer_version"] != NORMALIZER_VERSION
                or handoff["inventory"] != quality
            ):
                raise HandoffError("Archive provenance or quality checksum check failed.")
            batches = {item["date"]: item for item in handoff["source_batches"]}
            expected_days = {
                str(manifest.interval.start_date + timedelta(days=offset))
                for offset in range(
                    (manifest.interval.end_date - manifest.interval.start_date).days + 1
                )
            }
            batch_ids = [str(UUID(item["batch_id"])) for item in handoff["source_batches"]]
            if (
                set(batches) != expected_days
                or len(batches) != len(handoff["source_batches"])
                or len(batch_ids) != len(set(batch_ids))
                or set(batch_ids) != set(handoff["source_batch_ids"])
                or any(not isinstance(item["coverage_complete"], bool) for item in batches.values())
            ):
                raise HandoffError("Archive daily source batch coverage is inconsistent.")
            count = 0
            with sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True) as db:
                rows = db.execute(
                    "SELECT hash,physical,day,observation,row_numbers FROM revisions "
                    "ORDER BY day,key,hash"
                )
                for revision_hash, physical, day, observation, row_numbers in rows:
                    revision = RowRevision(
                        revision_hash=revision_hash,
                        physical_key=physical,
                        row_numbers=json.loads(row_numbers),
                        observation=json.loads(observation),
                    )
                    _observation(revision.observation, revision_hash, physical)
                    batch = batches[day]
                    if (
                        str(revision.observation.acquired_at.date()) != day
                        or revision.observation.product != manifest.product
                        or not manifest.interval.start_date
                        <= revision.observation.acquired_at.date()
                        <= manifest.interval.end_date
                    ):
                        raise HandoffError("Archive revision day mismatch.")
                    record = {
                        "import_id": identity,
                        "batch_id": str(UUID(batch["batch_id"])),
                        "coverage_complete": batch["coverage_complete"],
                        "revision": revision.model_dump(mode="json"),
                    }
                    stream.write(
                        (json.dumps(record, sort_keys=True, allow_nan=False) + "\n").encode()
                    )
                    count += 1
                distinct = db.execute("SELECT count(DISTINCT key) FROM revisions").fetchone()[0]
            if count != quality["revision_count"] or distinct != quality["accepted_count"]:
                raise HandoffError("Archive revision count check failed.")
            if quality["fetched_count"] != (
                quality["accepted_count"] + quality["rejected_count"] + quality["duplicate_count"]
            ):
                raise HandoffError("Archive quality totals do not reconcile.")
            for destination, key in (
                ("fetched", "fetched_count"),
                ("accepted", "accepted_count"),
                ("rejected", "rejected_count"),
                ("duplicates", "duplicate_count"),
                ("revisions", "revision_count"),
            ):
                counts[destination] += quality[key]
            datasets.append(
                {
                    "import_id": identity,
                    "source_manifest": manifest.model_dump(mode="json"),
                    "handoff_sha256": hash_file(path),
                    "quality_sha256": hash_file(quality_path),
                    "index_sha256": hash_file(index),
                    "source_batches": handoff["source_batches"],
                    "quality": quality,
                }
            )
    records = output / "archive-revisions.jsonl"
    temporary.replace(records)
    live: list[dict[str, Any]] = []
    refresh_ids: set[str] = set()
    for path in refreshes:
        handoff = _json(path)
        state = RefreshState.model_validate(handoff["state"])
        summary = RefreshSummary.model_validate(handoff["summary"])
        source = Path(handoff["normalized_records_path"])
        scan_secrets([path, source], secrets)
        if (
            hash_file(source) != handoff["normalized_records_sha256"]
            or summary != summarize_refresh(state)
            or handoff["schema_version"] != "firms-refresh-handoff-v1"
        ):
            raise HandoffError("Refresh provenance checksum check failed.")
        identity = str(state.refresh_id)
        if identity in refresh_ids:
            raise HandoffError("Refresh is repeated.")
        refresh_ids.add(identity)
        eligible = {str(c.batch_id): c for c in state.chunks if c.status == "succeeded"}
        if set(handoff["source_batch_ids"]) != set(eligible):
            raise HandoffError("Refresh source batch coverage mismatch.")
        count = 0
        observed: set[str] = set()
        with source.open(encoding="utf-8") as stream:
            for line in stream:
                item = SelectedDetection.model_validate_json(line)
                _observation(item.observation, item.revision_hash, item.physical_key)
                if (
                    str(item.batch_id) not in eligible
                    or item.observation.dedup_key in observed
                    or item.observation.product != eligible[str(item.batch_id)].request.product
                    or not summary.window_start
                    <= item.observation.acquired_at
                    <= summary.window_end
                ):
                    raise HandoffError("Refresh selection window or batch check failed.")
                observed.add(item.observation.dedup_key)
                count += 1
        if count != handoff["normalized_records_count"]:
            raise HandoffError("Refresh record count check failed.")
        live_target = output / ("refresh-" + identity + ".jsonl")
        with source.open("rb") as reader, live_target.open("wb") as writer:
            while block := reader.read(1024 * 1024):
                writer.write(block)
        if hash_file(live_target) != handoff["normalized_records_sha256"]:
            raise HandoffError("Refresh records changed during export.")
        live.append(
            {
                "normalized_records_file": live_target.name,
                "refresh_id": str(state.refresh_id),
                "handoff_sha256": hash_file(path),
                "normalized_records_sha256": hash_file(source),
                "normalized_records_count": count,
                "summary": summary.model_dump(mode="json"),
            }
        )
    report: dict[str, Any] = {
        "schema_version": "firms-module-handoff-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "normalizer_version": NORMALIZER_VERSION,
        "observation_key_version": KEY_VERSION,
        "observation_schema": "DetectionInput",
        "units": UNITS,
        "archive_totals": counts,
        "datasets": datasets,
        "refreshes": live,
        "files_sha256": {records.name: hash_file(records)},
        "evaluation": {
            "independent_event_count": None,
            "ready_for_event_separated_evaluation": False,
            "ready_for_supervised_training": False,
            "limitations": [
                "Activity periods are temporal proxies, not reviewed independent fire events.",
                "Labels and event-group assignments have not been supplied or verified.",
                "Historical weather and baseline context are outside this source handoff.",
                "Counts sum source imports; overlapping imports are not a unique dataset count.",
            ],
        },
        "consumer_contract": {
            "archive_records": records.name,
            "selection": "Select batches/product/cutoff with RevisionPolicy; freeze inputs.",
            "max_batches_per_selection": 128,
            "corrected_values": "Read corrected measurements from selected revisions.",
            "empty_coverage": "Missing/partial coverage is unknown, never zero fires.",
            "pipeline_run_completed": False,
        },
    }
    atomic_json(output / "observation.schema.json", DetectionInput.model_json_schema())
    atomic_json(
        output / "ingestion-quality.json",
        {
            "schema_version": "firms-ingestion-quality-v1",
            "totals": counts,
            "evaluation": report["evaluation"],
            "archives": [{"import_id": d["import_id"], "quality": d["quality"]} for d in datasets],
            "refreshes": live,
        },
    )
    for name in ["observation.schema.json", "ingestion-quality.json"] + [
        item["normalized_records_file"] for item in live
    ]:
        report["files_sha256"][name] = hash_file(output / name)
    # Scan final serialized manifest before publishing it, including URLs and provider metadata.
    pending = output / "manifest.pending.json"
    atomic_json(pending, report)
    scan_secrets([p for p in output.iterdir() if p.is_file()], secrets)
    pending.replace(output / "manifest.json")
    return report
