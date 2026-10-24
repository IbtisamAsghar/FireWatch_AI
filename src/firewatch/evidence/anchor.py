"""Pure AlertRegistry wire identities. Transaction/outbox execution belongs to Phase 5.4."""

import hashlib
import re
from uuid import UUID

ALERT_KEY_DOMAIN = b"firewatch-alert-key-v1\x00"


def alert_key(alert_id: UUID) -> str:
    """Domain-separated SHA-256 of the UUID's 16 bytes, encoded as EVM bytes32 hex."""
    if not isinstance(alert_id, UUID):
        raise ValueError("Alert key requires a UUID")
    return "0x" + hashlib.sha256(ALERT_KEY_DOMAIN + alert_id.bytes).hexdigest()


def evidence_digest(payload: bytes, original_hash: str) -> str:
    """Use the saved SHA-256 directly; never hash hex text or substitute keccak/SHA3."""
    if (
        not re.fullmatch(r"[0-9a-f]{64}", original_hash)
        or original_hash == "0" * 64
        or hashlib.sha256(payload).hexdigest() != original_hash
    ):
        raise ValueError("Evidence bytes must match a nonzero saved SHA-256 digest")
    return "0x" + original_hash
