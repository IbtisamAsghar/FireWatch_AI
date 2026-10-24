"""Backend/AlertRegistry wire compatibility without RPC, keys, settings or a database."""

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from firewatch.evidence.anchor import alert_key, evidence_digest


def test_alert_key_golden_domain_and_uuid_byte_order():
    identifier = UUID("00000000-0000-4000-8000-000000000001")
    assert alert_key(identifier) == (
        "0xb693673cfc1ede01f3c0b6f1207e6fe878759a4d08c1c61703c91d3e52980f45"
    )
    assert alert_key(identifier) != "0x" + hashlib.sha256(str(identifier).encode()).hexdigest()
    assert alert_key(identifier) != "0x" + hashlib.sha256(identifier.bytes).hexdigest()
    assert alert_key(uuid4()) != alert_key(identifier)


@pytest.mark.parametrize("identifier", ["invalid", "00000000-0000-4000-8000-000000000001", 1, None])
def test_alert_key_requires_typed_uuid(identifier):
    with pytest.raises(ValueError):
        alert_key(identifier)


def test_digest_is_original_sha256_not_hash_of_hex_or_sha3():
    payload = b'{"synthetic":true,"model_version":"baseline-v1"}'
    checksum = hashlib.sha256(payload).hexdigest()
    assert evidence_digest(payload, checksum) == "0x" + checksum
    assert bytes.fromhex(evidence_digest(payload, checksum)[2:]) == hashlib.sha256(payload).digest()
    for wrong in [
        hashlib.sha256(checksum.encode()).hexdigest(),
        hashlib.sha3_256(payload).hexdigest(),
    ]:
        with pytest.raises(ValueError):
            evidence_digest(payload, wrong)


@pytest.mark.parametrize("checksum", ["0" * 64, "f" * 63, "G" * 64, "0x" + "a" * 64, "A" * 64])
def test_zero_invalid_or_noncanonical_digests_rejected(checksum):
    with pytest.raises(ValueError):
        evidence_digest(b"synthetic", checksum)


def test_canonical_example_passes_directly_and_tampered_copy_is_withheld():
    root = Path(__file__).resolve().parents[2]
    original = (root / "docs/evidence-v1-example.json").read_bytes()
    checksum = hashlib.sha256(original).hexdigest()
    payload = json.loads(original)
    assert len(bytes.fromhex(alert_key(UUID(payload["alert_id"]))[2:])) == 32
    assert evidence_digest(original, checksum) == "0x" + checksum
    payload["priority_score"] = "999"
    with pytest.raises(ValueError):
        evidence_digest(json.dumps(payload).encode(), checksum)
    assert hashlib.sha256(original).hexdigest() == checksum
