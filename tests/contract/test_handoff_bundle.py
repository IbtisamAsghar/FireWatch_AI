"""The frontend receives a complete, secret-free, machine-readable handoff."""

import hashlib
import json

import pytest

from firewatch.db.base import SCHEMA_REVISION
from scripts.demo_data import read_requests
from scripts.export_handoff import export_handoff

pytestmark = pytest.mark.integration


def test_handoff_covers_all_reads_and_checksums_without_operator_secret(tmp_path, demo_settings):
    manifest = export_handoff(tmp_path, demo_settings)
    examples = json.loads((tmp_path / "examples.json").read_text())["examples"]
    assert set(read_requests()) <= examples.keys()
    assert manifest["read_operations_demonstrated"] == 15 and manifest["total_operations"] == 18
    assert manifest["synthetic"] is True and manifest["migration_revision"] == SCHEMA_REVISION
    for name, digest in manifest["files_sha256"].items():
        content = (tmp_path / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
        assert demo_settings.admin_api_token.get_secret_value().encode() not in content
        assert demo_settings.database_url.get_secret_value().encode() not in content
    dictionary = json.loads((tmp_path / "field-dictionary.json").read_text())
    assert dictionary["components"]["schemas"]["HotspotView"]["properties"][
        "escalation_probability"
    ]["anyOf"]
    assert dictionary["feature_units"]["wind_ms"] == "m/s"
