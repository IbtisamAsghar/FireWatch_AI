"""Versioned FIRMS normalization shared by bounded fetches and later archive import."""

import hashlib
import json
import math
import re
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from pydantic import Field, JsonValue

from firewatch.schemas.common import DTO, Confidence, DayNight, Digest, Product, Sensor
from firewatch.schemas.contracts import DetectionBatch, DetectionInput, SourceAssetInput
from firewatch.sources.firms import FirmsAreaRequest, FirmsBatch, parse_firms_csv

NORMALIZER_VERSION = "firms-normalizer-v1"
KEY_VERSION = "firms-observation-v1"


class NormalizationPolicy(DTO):
    invalid_frp: Literal["reject", "null"] = "reject"
    missing_frp: Literal["allow", "reject"] = "allow"
    rejection_samples: int = Field(default=20, ge=0, le=100)


class RejectReason(StrEnum):
    COORDINATE = "invalid_coordinate"
    BBOX = "outside_requested_bbox"
    ACQUISITION = "invalid_acquisition_datetime"
    WINDOW = "outside_requested_utc_window"
    SENSOR = "sensor_product_mismatch"
    INSTRUMENT = "unexpected_instrument"
    FRP = "invalid_frp"
    MISSING_FRP = "missing_required_frp"
    PROCESSING = "processing_product_mismatch"


class RejectedRow(DTO):
    row_number: int
    reasons: list[RejectReason]
    raw_values: dict[str, str]


class RowRevision(DTO):
    revision_hash: Digest
    physical_key: Digest
    row_numbers: list[int]
    observation: DetectionInput


class QualityReport(DTO):
    schema_version: Literal["firms-quality-v1"] = "firms-quality-v1"
    normalizer_version: str = NORMALIZER_VERSION
    observation_key_version: str = KEY_VERSION
    policy: NormalizationPolicy
    policy_hash: Digest
    source_batch_id: UUID
    source_sha256: Digest
    product: Product
    fetched_count: int
    accepted_count: int
    rejected_count: int
    duplicate_count: int
    revised_count: int
    rejection_counts: dict[str, int]
    quality_flag_counts: dict[str, int]
    rejection_samples: list[dict[str, JsonValue]]


class NormalizedFirmsBatch(DTO):
    schema_version: Literal["firms-normalized-v1"] = "firms-normalized-v1"
    source_batch_id: UUID
    request: FirmsAreaRequest
    asset: SourceAssetInput
    revisions: list[RowRevision]
    report: QualityReport
    rejections: list[RejectedRow]

    def detection_batch(self, region_id: UUID) -> DetectionBatch:
        first = {r.observation.dedup_key: r.observation for r in reversed(self.revisions)}
        start = datetime.combine(self.request.start_date, time.min, UTC)
        return DetectionBatch(
            asset_id=self.source_batch_id,
            batch_id=uuid5(self.source_batch_id, f"{self.report.policy_hash}:{region_id}"),
            region_id=region_id,
            product=self.request.product,
            window_start=start,
            window_end=start + timedelta(days=self.request.days),
            detections=list(first.values()),
            rejected_count=len(self.rejections),
        )


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def decimal_value(value: str) -> Decimal:
    text = value.strip()
    if len(text) > 100 or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", text):
        raise ValueError("Invalid decimal")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError("Invalid decimal") from None
    if not number.is_finite():
        raise ValueError("Nonfinite decimal")
    return number


def canonical_decimal(number: Decimal) -> str:
    if number == 0:
        return "0"
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def optional_number(
    row: dict[str, str], column: str, flags: list[str], *, positive: bool = False
) -> tuple[float | None, str | None, bool]:
    raw = row.get(column, "")
    if not raw.strip():
        flags.append(column + "_missing")
        return None, None, False
    try:
        number = decimal_value(raw)
        value = float(number)
        if not math.isfinite(value) or value < 0 or (positive and value <= 0):
            raise ValueError("Invalid measurement")
        if column.startswith("bright") and value < 0.000001:
            raise ValueError("Below database physical-value bound")
        return value, canonical_decimal(number), False
    except ValueError:
        flags.append(column + "_invalid")
        return None, "invalid:" + hashlib.sha256(raw.encode()).hexdigest(), True


def normalize_row(
    row: dict[str, str], request: FirmsAreaRequest, policy: NormalizationPolicy
) -> tuple[DetectionInput | None, str | None, list[RejectReason]]:
    reasons: list[RejectReason] = []
    flags: list[str] = []
    try:
        lat, lon = decimal_value(row.get("latitude", "")), decimal_value(row.get("longitude", ""))
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValueError("Coordinate bound")
        box = request.bbox
        if not Decimal(str(box.south)) <= lat <= Decimal(str(box.north)) or not Decimal(
            str(box.west)
        ) <= lon <= Decimal(str(box.east)):
            reasons.append(RejectReason.BBOX)
    except ValueError:
        reasons.append(RejectReason.COORDINATE)
        lat = lon = Decimal(0)
    try:
        day, clock = row.get("acq_date", "").strip(), row.get("acq_time", "").strip()
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day) or not re.fullmatch(
            r"[0-9]{1,4}", clock
        ):
            raise ValueError("Invalid acquisition syntax")
        acquired = datetime.combine(
            date.fromisoformat(day), datetime.strptime(clock.zfill(4), "%H%M").time(), UTC
        )
        if (
            not request.start_date
            <= acquired.date()
            < request.start_date + timedelta(days=request.days)
        ):
            reasons.append(RejectReason.WINDOW)
    except ValueError:
        reasons.append(RejectReason.ACQUISITION)
        acquired = datetime(1970, 1, 1, tzinfo=UTC)
    sensor = Sensor.NOAA20 if "NOAA20" in request.product else Sensor.NOAA21
    aliases = {
        Sensor.NOAA20: {"N20", "20", "1", "NOAA-20", "JPSS-1", "J1"},
        Sensor.NOAA21: {"N21", "21", "2", "NOAA-21", "JPSS-2", "J2"},
    }
    if row.get("satellite", "").strip().upper() not in aliases[sensor]:
        reasons.append(RejectReason.SENSOR)
    if row.get("instrument", "").strip().upper() != "VIIRS":
        reasons.append(RejectReason.INSTRUMENT)
    frp, _, invalid_frp = optional_number(row, "frp", flags)
    if invalid_frp and policy.invalid_frp == "reject":
        reasons.append(RejectReason.FRP)
    if not row.get("frp", "").strip() and policy.missing_frp == "reject":
        reasons.append(RejectReason.MISSING_FRP)
    brightness, _, _ = optional_number(row, "bright_ti4", flags, positive=True)
    brightness5, _, _ = optional_number(row, "bright_ti5", flags, positive=True)
    scan, scan_key, _ = optional_number(row, "scan", flags, positive=True)
    track, track_key, _ = optional_number(row, "track", flags, positive=True)
    raw_confidence = row.get("confidence", "")
    confidence = {
        "l": Confidence.LOW,
        "low": Confidence.LOW,
        "n": Confidence.NOMINAL,
        "nominal": Confidence.NOMINAL,
        "h": Confidence.HIGH,
        "high": Confidence.HIGH,
    }.get(raw_confidence.strip().lower(), Confidence.UNKNOWN)
    if confidence == Confidence.UNKNOWN:
        flags.append("confidence_unknown" if raw_confidence.strip() else "confidence_missing")
    raw_daynight = row.get("daynight", "").strip().upper()
    daynight = DayNight(raw_daynight) if raw_daynight in {"D", "N"} else None
    if daynight is None:
        flags.append("daynight_unknown")
    version = row.get("version", "").strip().upper()
    match = re.fullmatch(r"[0-9]+(?:\.[0-9]+)*(NRT|URT|RT)?", version)
    processing = "unknown"
    if match:
        processing = match.group(1) or "SP"
        if request.product.endswith("_SP") != (processing == "SP"):
            reasons.append(RejectReason.PROCESSING)
    else:
        flags.append("processing_version_unknown")
    if reasons:
        return None, None, reasons
    identity = {
        "key_version": KEY_VERSION,
        "sensor": sensor.value,
        "acquired_at": acquired.isoformat(),
        "latitude": canonical_decimal(lat),
        "longitude": canonical_decimal(lon),
        "scan": scan_key,
        "track": track_key,
    }
    physical_key = digest(identity)
    raw_values: dict[str, JsonValue] = {
        "provider_row": dict(row),
        "normalization": {
            "version": NORMALIZER_VERSION,
            "key_version": KEY_VERSION,
            "physical_key": physical_key,
            "processing_status": processing,
            "latitude_decimal_places": max(0, -int(lat.as_tuple().exponent)),
            "longitude_decimal_places": max(0, -int(lon.as_tuple().exponent)),
            "bright_ti5_k": brightness5,
            "units": {
                "frp_mw": "MW",
                "brightness_k": "K",
                "bright_ti5_k": "K",
                "scan": "km",
                "track": "km",
            },
        },
    }
    return (
        DetectionInput(
            product=request.product,
            sensor=sensor,
            latitude=float(lat),
            longitude=float(lon),
            acquired_at=acquired,
            frp_mw=frp,
            brightness_k=brightness,
            confidence_raw=raw_confidence,
            confidence=confidence,
            daynight=daynight,
            scan=scan,
            track=track,
            raw_values=raw_values,
            quality_flags=flags,
            dedup_key=digest(identity | {"product": request.product.value}),
        ),
        physical_key,
        [],
    )


def normalize_firms_batch(
    raw: FirmsBatch, policy: NormalizationPolicy | None = None
) -> NormalizedFirmsBatch:
    policy = NormalizationPolicy.model_validate((policy or NormalizationPolicy()).model_dump())
    request = FirmsAreaRequest.model_validate(raw.request.model_dump(mode="json"))
    try:
        request.start_date + timedelta(days=request.days)
    except OverflowError:
        raise ValueError("Normalization requires a representable exclusive window end") from None
    if (
        raw.row_count != len(raw.rows)
        or raw.empty != (not raw.rows)
        or raw.asset.product != request.product
        or raw.asset.provider != "firms"
        or raw.asset.request_metadata != request.model_dump(mode="json")
        or raw.asset.retrieved_at != raw.retrieved_at
    ):
        raise ValueError("Raw batch metadata is inconsistent")
    revisions: dict[str, RowRevision] = {}
    keys: set[str] = set()
    rejected: list[RejectedRow] = []
    flag_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for number, row in enumerate(raw.rows, start=2):
        observation, physical_key, reasons = normalize_row(row, request, policy)
        if observation is None:
            rejected.append(RejectedRow(row_number=number, reasons=reasons, raw_values=row))
            reason_counts.update(reasons)
            continue
        assert physical_key is not None
        flag_counts.update(observation.quality_flags)
        keys.add(observation.dedup_key)
        revision_hash = digest(observation.model_dump(mode="json"))
        if revision_hash in revisions:
            revisions[revision_hash].row_numbers.append(number)
        else:
            revisions[revision_hash] = RowRevision(
                revision_hash=revision_hash,
                physical_key=physical_key,
                row_numbers=[number],
                observation=observation,
            )
    samples: list[dict[str, JsonValue]] = []
    for item in rejected[: policy.rejection_samples]:
        columns = list(item.raw_values)[:20]
        samples.append(
            {
                "row_number": item.row_number,
                "reasons": [reason.value for reason in item.reasons],
                "raw_values": {key: item.raw_values[key][:200] for key in columns},
                "truncated": len(columns) < len(item.raw_values)
                or any(len(value) > 200 for value in item.raw_values.values()),
            }
        )
    report = QualityReport(
        policy=policy,
        policy_hash=digest({"version": NORMALIZER_VERSION, "policy": policy.model_dump()}),
        source_batch_id=raw.batch_id,
        source_sha256=raw.asset.content_hash,
        product=request.product,
        fetched_count=len(raw.rows),
        accepted_count=len(keys),
        rejected_count=len(rejected),
        duplicate_count=len(raw.rows) - len(rejected) - len(keys),
        revised_count=len(revisions) - len(keys),
        rejection_counts=dict(reason_counts),
        quality_flag_counts=dict(flag_counts),
        rejection_samples=samples,
    )
    return NormalizedFirmsBatch(
        source_batch_id=raw.batch_id,
        request=request,
        asset=raw.asset,
        revisions=list(revisions.values()),
        report=report,
        rejections=rejected,
    )


def load_saved_firms_batch(manifest_path: Path) -> FirmsBatch:
    """Read only a completed Phase 2.2 manifest; generic archive import is Phase 2.4."""
    if manifest_path.stat().st_size > 1024 * 1024:
        raise ValueError("Manifest exceeds its bound")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "firms-request-manifest-v1" or manifest.get(
        "status"
    ) not in {"succeeded", "empty"}:
        raise ValueError("A completed Phase 2.2 fetch manifest is required")
    request = FirmsAreaRequest.model_validate(manifest["request"])
    asset = SourceAssetInput.model_validate(manifest["asset"])
    area = manifest["responses"][-1]
    if area["kind"] != "area" or not area["complete"] or area["http_status"] != 200:
        raise ValueError("Area response is not complete")
    filename = area["filename"]
    if not re.fullmatch(r"response-[0-9]+\.bin", filename):
        raise ValueError("Invalid raw evidence filename")
    path = (manifest_path.parent / filename).resolve()
    if path.parent != manifest_path.parent.resolve() or path != Path(asset.storage_path).resolve():
        raise ValueError("Raw evidence must remain inside its owning fetch directory")
    with path.open("rb") as stream:
        content = stream.read(32 * 1024 * 1024 + 1)
    if len(content) > 32 * 1024 * 1024 or len(content) != area["byte_count"]:
        raise ValueError("Raw evidence size is invalid")
    checksum = hashlib.sha256(content).hexdigest()
    if checksum != area["sha256"] or checksum != asset.content_hash:
        raise ValueError("Raw evidence checksum does not match")
    columns, rows = parse_firms_csv(content, max_rows=100000)
    if (
        len(rows) != manifest["row_count"]
        or asset.product != request.product
        or asset.provider != "firms"
    ):
        raise ValueError("Manifest product, provider or row count is inconsistent")
    if asset.request_metadata != request.model_dump(mode="json"):
        raise ValueError("Source request metadata is inconsistent")
    result = FirmsBatch(
        batch_id=manifest["batch_id"],
        request=request,
        retrieved_at=manifest["retrieved_at"],
        asset=asset,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        empty=not rows,
        processing_versions=sorted({row["version"] for row in rows if row["version"]}),
        manifest_path=manifest_path.resolve(),
    )
    if result.retrieved_at != asset.retrieved_at:
        raise ValueError("Manifest retrieval timestamp is inconsistent")
    return result


def write_quality_reports(result: NormalizedFirmsBatch, directory: Path) -> tuple[Path, Path]:
    """Small summary plus full rejected-row JSONL; original CSV remains authoritative."""
    stem = result.report.policy_hash[:16]
    summary = directory / f"quality-{stem}.json"
    rejections = directory / f"rejected-{stem}.jsonl"
    directory.mkdir(parents=True, exist_ok=True)
    for path, text in (
        (summary, result.report.model_dump_json(indent=2) + "\n"),
        (rejections, "".join(row.model_dump_json() + "\n" for row in result.rejections)),
    ):
        temp = path.with_suffix(".tmp-" + uuid4().hex)
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
    return summary, rejections
