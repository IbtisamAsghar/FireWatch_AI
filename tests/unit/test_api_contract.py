"""Dependency failure behavior, OpenAPI contracts and public projection safety."""

import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from firewatch.api.presentation import ArtifactInspector, evidence_projection, public_json
from firewatch.config import Settings
from firewatch.db.models import Alert, ModelVersion
from firewatch.db.session import get_session
from firewatch.main import create_app


def settings(**kwargs):
    return Settings(
        database_url="postgresql+psycopg://test:password@localhost:1/firewatch_test",
        admin_api_token="test-operator-" + "x" * 40,
        _env_file=None,
        **kwargs,
    )


def test_openapi_operations_are_typed_unique_and_stable():
    document = create_app().openapi()
    operations = [item["get"] for item in document["paths"].values() if "get" in item]
    assert len(operations) == 15
    expected = {
        "health_live",
        "health_ready",
        "list_regions",
        "get_status",
        "list_runs",
        "get_run",
        "list_hotspots",
        "get_hotspot",
        "get_hotspots_geojson",
        "get_summary",
        "get_current_model",
        "list_replays",
        "get_alert",
        "verify_alert",
        "get_job",
    }
    assert {op["operationId"] for op in operations} == expected
    for operation in operations:
        assert operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert document["paths"]["/api/v1/jobs/{job_id}"]["get"]["security"] == [{"OperatorBearer": []}]
    params = document["paths"]["/api/v1/hotspots"]["get"]["parameters"]
    assert next(p for p in params if p["name"] == "run_id")["required"]
    assert next(p for p in params if p["name"] == "limit")["schema"]["maximum"] == 200


def test_dependency_failure_is_503_but_live_does_not_connect(caplog):
    app = create_app(settings())

    def offline():
        raise OperationalError("PRIVATE_DATABASE_SECRET", {}, Exception("PRIVATE_DATABASE_SECRET"))
        yield

    app.dependency_overrides[get_session] = offline
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        for path in ("/health/ready", "/api/v1/status", "/api/v1/regions"):
            response = client.get(path)
            assert response.status_code == 503 and response.json()["error"]["retryable"]
            assert "PRIVATE_DATABASE_SECRET" not in response.text
    assert "PRIVATE_DATABASE_SECRET" not in caplog.text


def test_unhandled_error_is_sanitized(caplog):
    app = create_app(settings())

    def broken():
        raise RuntimeError("PRIVATE_INTERNAL_SECRET")
        yield

    app.dependency_overrides[get_session] = broken
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/status")
        assert response.status_code == 500
        assert "PRIVATE_INTERNAL_SECRET" not in response.text
        assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]
    # Exception values and SQL are excluded; traceback source frames remain useful diagnostics.
    assert "PRIVATE_INTERNAL_SECRET" not in caplog.text


def test_projection_never_changes_original_evidence_bytes():
    raw = b'{"temperature_c":30,"token":"PRIVATE_TOKEN","nested":{"url":"https://key.example/secret"}}'
    record = Alert(
        id=uuid4(),
        canonical_payload=raw,
        original_hash=hashlib.sha256(raw).hexdigest(),
    )
    projected, redacted = evidence_projection(record)
    assert projected == {"temperature_c": 30, "nested": {}}
    assert redacted and record.canonical_payload == raw
    assert public_json({"note": "postgresql://private", "bad": float("nan")}) == {
        "note": "[redacted]",
        "bad": None,
    }


@pytest.mark.parametrize("location", ["../outside.bin", "missing.bin"])
def test_artifact_inspector_rejects_outside_root_and_missing(tmp_path, location):
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"test")
    model = ModelVersion(artifact_path=location, artifact_hash=hashlib.sha256(b"test").hexdigest())
    assert ArtifactInspector().ready(model, tmp_path) is False
    assert Path(outside).is_file()


def test_verification_reports_local_tampering_independently_of_chain():
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from firewatch.api.presentation import verification
    from firewatch.db.models import Run
    from firewatch.schemas.common import Completeness, RunMode

    now = datetime(2025, 6, 10, tzinfo=UTC)
    run = Run(
        id=uuid4(),
        mode=RunMode.REPLAY,
        as_of=now,
        input_asset_manifest={},
        completeness=Completeness.COMPLETE,
        scorer_version="baseline-v1",
    )
    record = Alert(id=uuid4(), canonical_payload=b"tampered", original_hash="a" * 64)
    session = MagicMock()
    session.scalar.return_value = None
    session.scalars.return_value.all.return_value = []
    result = verification(session, record, run, now, settings(), None)
    assert result.local_status == "local_mismatch"
    assert result.computed_hash == hashlib.sha256(b"tampered").hexdigest()
    assert result.anchor_status == "unanchored"
    session.add.assert_not_called()
    session.commit.assert_not_called()
