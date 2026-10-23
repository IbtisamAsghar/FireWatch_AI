"""Versioned evidence wire encoding; legacy package serialization stays unchanged."""

import json
import math
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Any
from uuid import UUID

CANONICALIZATION_VERSION = "evidence-json-v1"
MAX_EVIDENCE_BYTES = 32 * 1024 * 1024
DECIMAL_PATTERN = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?$"
# Set semantics only. Reasons, model input order, SHAP paths and year intervals
# are positional and MUST NOT be sorted.
UNORDERED_FIELDS = frozenset(
    {
        "warnings",
        "quality_flags",
        "source_asset_ids",
        "member_ids",
        "batch_ids",
        "assets",
        "satellite_assets",
        "member_revisions",
        "selected",
        "records",
        "source_checksums",
    }
)
TIMESTAMP_FIELDS = frozenset(
    {
        "as_of",
        "scored_at",
        "valid_at",
        "retrieved_at",
        "retrieval_cutoff",
        "window_start",
        "window_end",
        "latest_detection_at",
        "earliest_snapshot",
        "precipitation_interval_end",
    }
)


def decimal_string(value: float) -> str:
    """Binary64 -> 17 significant decimal digits, nearest/ties-even, no exponent."""
    if not math.isfinite(value):
        raise ValueError("Evidence numbers must be finite")
    with localcontext(Context(prec=17, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999)):
        rounded = +Decimal.from_float(value)
    if rounded.is_zero():
        return "0"
    result = format(rounded, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Evidence timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def wire_value(value: Any, field: str = "", depth: int = 0) -> Any:
    if depth > 64:
        raise ValueError("Evidence nesting exceeds bound")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, str):
        value.encode("utf-8", errors="strict")
        if field in TIMESTAMP_FIELDS:
            return utc_text(datetime.fromisoformat(value.replace("Z", "+00:00")))
        return value
    if isinstance(value, int):
        if abs(value) > 2**53 - 1:
            raise ValueError("Evidence integers must fit the interoperable exact range")
        return value
    if isinstance(value, float):
        return decimal_string(value)
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Evidence object keys must be strings")
        return {key: wire_value(child, key, depth + 1) for key, child in value.items()}
    if isinstance(value, list | tuple):
        result = [wire_value(child, "", depth + 1) for child in value]
        if field in UNORDERED_FIELDS:
            result.sort(key=_json)
        return result
    raise ValueError("Unsupported evidence value type")


def serialize_evidence(payload: dict[str, Any]) -> bytes:
    if payload.get("schema_version") != "evidence-v1" or (
        payload.get("canonicalization_version") != CANONICALIZATION_VERSION
    ):
        raise ValueError("Unsupported evidence/canonicalization version")
    raw = _json(wire_value(payload)).encode("utf-8", errors="strict")
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("Evidence exceeds byte bound")
    return raw
