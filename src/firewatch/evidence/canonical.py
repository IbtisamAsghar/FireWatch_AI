"""evidence-v1: deterministic UTF-8 sorted JSON without nonfinite numbers."""

import hashlib
import json
from typing import Any


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def evidence_hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
