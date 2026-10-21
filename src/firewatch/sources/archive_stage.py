"""Streaming preflight and disk-backed inventory; never retain a seasonal CSV in RAM."""

import csv
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from firewatch.sources.archive_manifest import ARCHIVE_VERSION, ArchiveError, ArchiveManifest
from firewatch.sources.firms import valid_firms_columns
from firewatch.sources.firms_normalize import (
    NORMALIZER_VERSION,
    NormalizationPolicy,
    digest,
    normalize_row,
)


@dataclass(frozen=True)
class PreparedArchive:
    import_id: UUID
    manifest: ArchiveManifest
    policy: NormalizationPolicy
    folder: Path
    index_path: Path
    quality: dict[str, Any]

    def selectable(self, day: date) -> bool:
        return self.manifest.covered(day) and not any(
            item["start_date"] <= str(day) <= item["end_date"]
            for item in self.quality["unexpectedly_empty_intervals"]
        )


def atomic_json(path: Path, value: Any) -> None:
    temp = path.with_name(path.name + ".tmp-" + uuid4().hex)
    temp.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def hash_file(path: Path, max_bytes: int = 2 * 1024**3) -> str:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            size += len(block)
            if size > max_bytes:
                raise ArchiveError("CSV exceeds the configured archive size bound.")
            hasher.update(block)
    return hasher.hexdigest()


def preserve_original(path: Path, target: Path, checksum: str, max_bytes: int) -> None:
    if target.exists():
        if hash_file(path, max_bytes) != checksum or hash_file(target, max_bytes) != checksum:
            raise ArchiveError("Original CSV checksum does not match the manifest.")
        return
    temp = target.with_name("original.tmp-" + uuid4().hex)
    try:
        hasher = hashlib.sha256()
        size = 0
        with path.open("rb") as source, temp.open("xb") as output:
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise ArchiveError("CSV exceeds the configured archive size bound.")
                hasher.update(block)
                output.write(block)
        if hasher.hexdigest() != checksum:
            raise ArchiveError("Original CSV checksum does not match the manifest.")
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)


def intervals(days: list[date]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for day in sorted(set(days)):
        if result and date.fromisoformat(result[-1]["end_date"]) + timedelta(days=1) == day:
            result[-1]["end_date"] = str(day)
        else:
            result.append({"start_date": str(day), "end_date": str(day)})
    return result


def prepare_archive(
    path: Path,
    manifest: ArchiveManifest,
    output: Path,
    *,
    policy: NormalizationPolicy | None = None,
    chunk_rows: int = 1000,
    max_bytes: int = 2 * 1024**3,
    max_rows: int = 10000000,
) -> PreparedArchive:
    if not 1 <= chunk_rows <= 5000 or max_bytes < 1 or max_rows < 1:
        raise ArchiveError("Invalid archive processing bounds.")
    manifest = ArchiveManifest.model_validate(manifest.model_dump(mode="json"))
    policy = NormalizationPolicy.model_validate((policy or NormalizationPolicy()).model_dump())
    import_id = manifest.identity(policy)
    folder = output.resolve() / str(import_id)
    folder.mkdir(parents=True, exist_ok=True)
    original = folder / "original.csv"
    preserve_original(path, original, manifest.sha256, max_bytes)
    atomic_json(folder / "manifest.json", manifest.model_dump(mode="json"))
    temporary = folder / ("index-" + uuid4().hex + ".sqlite")
    reasons: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    day_counts: dict[str, Counter[str]] = {str(day): Counter() for day in manifest.days()}
    mixed = 0
    try:
        with (
            closing(sqlite3.connect(temporary)) as db,
            db,
            original.open(encoding="utf-8-sig", newline="") as stream,
            (folder / "rejected.jsonl").open("w", encoding="utf-8") as rejected,
        ):
            db.executescript("""
                CREATE TABLE revisions(hash TEXT PRIMARY KEY, key TEXT, day TEXT, physical TEXT,
                    observation TEXT, row_numbers TEXT, occurrences INTEGER);
                CREATE INDEX revisions_order ON revisions(day,key,hash);
                CREATE TABLE observations(key TEXT PRIMARY KEY, day TEXT, acquired TEXT,
                    confidence TEXT, provider_class TEXT, hash TEXT);
                CREATE TABLE row_provenance(number INTEGER PRIMARY KEY,
                    revision_hash TEXT, reasons TEXT);
            """)
            reader = csv.DictReader(stream, strict=True)
            fields = reader.fieldnames
            if not valid_firms_columns(fields):
                raise ArchiveError("Archive CSV has malformed or unsupported columns.")
            for number, row in enumerate(reader, start=2):
                counts["fetched"] += 1
                if (
                    counts["fetched"] > max_rows
                    or None in row
                    or any(value is None for value in row.values())
                ):
                    raise ArchiveError(
                        "Archive CSV exceeds its row bound or has malformed row widths."
                    )
                try:
                    parsed_day = date.fromisoformat(row.get("acq_date", ""))
                    day: date | None = (
                        parsed_day if manifest.interval.contains(parsed_day) else None
                    )
                except ValueError:
                    day = None
                request = manifest.request(day or manifest.interval.start_date)
                observation, physical, rejected_reasons = normalize_row(row, request, policy)
                row_reasons = [r.value for r in rejected_reasons]
                if day is None and "outside_archive_interval" not in row_reasons:
                    row_reasons.append("outside_archive_interval")
                if row.get("product", manifest.product.value) != manifest.product.value:
                    row_reasons.append("mixed_product")
                if any(
                    reason in row_reasons
                    for reason in (
                        "mixed_product",
                        "sensor_product_mismatch",
                        "processing_product_mismatch",
                    )
                ):
                    mixed += 1
                if day is not None:
                    day_counts[str(day)]["fetched"] += 1
                if row_reasons or observation is None:
                    counts["rejected"] += 1
                    reasons.update(row_reasons)
                    if day is not None:
                        day_counts[str(day)]["rejected"] += 1
                    rejected.write(
                        json.dumps(
                            {"row_number": number, "reasons": row_reasons, "raw_values": row}
                        )
                        + "\n"
                    )
                    db.execute(
                        "INSERT INTO row_provenance VALUES (?,?,?)",
                        (number, None, json.dumps(row_reasons)),
                    )
                else:
                    value = observation.model_dump(mode="json")
                    revision_hash = digest(value)
                    previous = db.execute(
                        "SELECT row_numbers,occurrences FROM revisions WHERE hash=?",
                        (revision_hash,),
                    ).fetchone()
                    if previous:
                        sample = json.loads(previous[0])
                        if len(sample) < 20:
                            sample.append(number)
                        db.execute(
                            "UPDATE revisions SET row_numbers=?,occurrences=occurrences+1 "
                            "WHERE hash=?",
                            (json.dumps(sample), revision_hash),
                        )
                    else:
                        db.execute(
                            "INSERT INTO revisions VALUES (?,?,?,?,?,?,1)",
                            (
                                revision_hash,
                                observation.dedup_key,
                                str(day),
                                physical,
                                json.dumps(value),
                                json.dumps([number]),
                            ),
                        )
                    db.execute(
                        "INSERT INTO observations VALUES (?,?,?,?,?,?) ON CONFLICT(key) "
                        "DO UPDATE SET hash=excluded.hash,confidence=excluded.confidence,"
                        "provider_class=excluded.provider_class "
                        "WHERE excluded.hash>observations.hash",
                        (
                            observation.dedup_key,
                            str(day),
                            observation.acquired_at.isoformat(),
                            observation.confidence.value,
                            row["type"] if row.get("type") in {"0", "1", "2", "3"} else "unknown",
                            revision_hash,
                        ),
                    )
                    db.execute(
                        "INSERT INTO row_provenance VALUES (?,?,NULL)", (number, revision_hash)
                    )
                    flags.update(observation.quality_flags)
                if counts["fetched"] % chunk_rows == 0:
                    db.commit()
            db.commit()
            excessive = db.execute(
                "SELECT key FROM revisions GROUP BY key HAVING count(*)>64 LIMIT 1"
            ).fetchone()
            if excessive:
                raise ArchiveError(
                    "An observation has too many correction variants for bounded lineage storage."
                )
            quality = inventory(db, manifest, day_counts, counts, reasons, flags)
            quality["mixed_product_rows"] = mixed
            quality["normalization_policy"] = policy.model_dump(mode="json")
            quality["normalizer_version"] = NORMALIZER_VERSION
            atomic_json(folder / "quality.json", quality)
        if mixed:
            raise ArchiveError(
                "Mixed sensor/product/processing rows detected; no database import was started."
            )
        index = folder / "index.sqlite"
        temporary.replace(index)
        return PreparedArchive(import_id, manifest, policy, folder, index, quality)
    except (csv.Error, UnicodeError):
        raise ArchiveError("Archive CSV is malformed or is not UTF-8.") from None
    finally:
        temporary.unlink(missing_ok=True)


def inventory(
    db: sqlite3.Connection,
    manifest: ArchiveManifest,
    day_counts: dict[str, Counter[str]],
    counts: Counter[str],
    reasons: Counter[str],
    flags: Counter[str],
) -> dict[str, Any]:
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    last: dict[tuple[str, str, str], datetime] = {}
    previous_time = None
    periods = 0
    for day, acquired, confidence, provider_class in db.execute(
        "SELECT day,acquired,confidence,provider_class FROM observations ORDER BY acquired,key"
    ):
        timestamp = datetime.fromisoformat(acquired)
        if previous_time is None or timestamp - previous_time > timedelta(hours=24):
            periods += 1
        previous_time = timestamp
        key = (day[:7], confidence, provider_class)
        item = buckets.setdefault(
            key,
            dict(
                month=day[:7],
                sensor=manifest.sensor.value,
                product=manifest.product.value,
                region=manifest.region,
                class_label="unlabeled",
                confidence_class=confidence,
                provider_class=provider_class,
                observations=0,
                distinct_fire_periods_proxy=0,
            ),
        )
        item["observations"] += 1
        if key not in last or timestamp - last[key] > timedelta(hours=24):
            item["distinct_fire_periods_proxy"] += 1
        last[key] = timestamp
        day_counts[day]["accepted"] += 1
    missing = [day for day in manifest.days() if not manifest.covered(day)]
    empty = [
        date.fromisoformat(day) for day, counter in day_counts.items() if counter["accepted"] == 0
    ]
    unexpected = [
        day for day in empty if any(i.contains(day) for i in manifest.expected_active_intervals)
    ]
    unconfirmed = [
        day for day in empty if not any(i.contains(day) for i in manifest.confirmed_empty_intervals)
    ]
    # Empty hotspot days are not missing coverage. Long/unconfirmed empty spans
    # warrant review; explicit complete no-fire declarations can resolve them.
    for span in intervals(unconfirmed):
        first, final = date.fromisoformat(span["start_date"]), date.fromisoformat(span["end_date"])
        if (final - first).days >= 6 or counts["fetched"] == 0:
            unexpected.extend(first + timedelta(days=i) for i in range((final - first).days + 1))
    months = {str(day)[:7] for day in manifest.days()}
    for month in sorted(months - {key[0] for key in buckets}):
        buckets[(month, "unknown", "unknown")] = dict(
            month=month,
            sensor=manifest.sensor.value,
            product=manifest.product.value,
            region=manifest.region,
            class_label="unlabeled",
            confidence_class="unknown",
            provider_class="unknown",
            observations=0,
            distinct_fire_periods_proxy=0,
        )
    observations = db.execute("SELECT count(*) FROM observations").fetchone()[0]
    revisions = db.execute("SELECT count(*) FROM revisions").fetchone()[0]
    return dict(
        schema_version=ARCHIVE_VERSION,
        source_sha256=manifest.sha256,
        source_kind=manifest.source_kind,
        fetched_count=counts["fetched"],
        accepted_count=observations,
        rejected_count=counts["rejected"],
        duplicate_count=counts["fetched"] - counts["rejected"] - observations,
        revision_count=revisions,
        revised_count=revisions - observations,
        rejection_counts=dict(reasons),
        quality_flag_counts=dict(flags),
        daily_counts={day: dict(c) for day, c in day_counts.items()},
        missing_date_intervals=intervals(missing),
        empty_intervals=intervals(empty),
        unexpectedly_empty_intervals=intervals(unexpected),
        unconfirmed_empty_intervals=intervals(unconfirmed),
        inventory=list(buckets.values()),
        distinct_fire_periods_proxy=periods,
        period_definition=(
            "Adjacent observation times separated by more than 24 hours; "
            "thermal-activity proxy, not independent fire events."
        ),
        training_classes={"unlabeled": observations, "positive": None, "negative": None},
        observation_history_complete=not missing and not counts["rejected"] and not unexpected,
        margins_hours=24,
        weather_history="not_imported",
        baseline_history="not_imported",
        ready_for_supervised_training=False,
        limitations=[
            "No training labels or independent event IDs have been supplied.",
            "Temporal activity periods do not prove event-separated evaluation is possible.",
            "Sparse hotspot rows cannot prove satellite/cloud-free observation coverage.",
        ],
    )
