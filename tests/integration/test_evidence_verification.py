"""Phase 5.2 HTTP/CLI audits against immutable, disposable PostgreSQL evidence."""

import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_worker import CLOCK, take, work  # noqa: F401
from typer.testing import CliRunner

from firewatch.db.models import Alert, Anchor, AnchorOutbox, Job, Run, SourceAsset
from firewatch.db.session import get_session
from firewatch.main import create_app
from firewatch.pipeline.runner import execute_run
from firewatch.schemas.common import AnchorStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def evidence_api(work):  # noqa: F811
    engine, config, _, _ = work
    run_id = execute_run(engine, config, take(engine, config), lambda: CLOCK)
    with Session(engine) as session:
        record = session.scalar(select(Alert).where(Alert.run_id == run_id))
        identifier, raw, checksum = record.id, record.canonical_payload, record.original_hash
    app = create_app(config)

    def sessions():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.clock = lambda: CLOCK
        yield client, engine, config, identifier, raw, checksum


def test_opt_in_http_audit_reads_original_bytes_and_preserves_all_state(evidence_api):
    client, engine, _, identifier, raw, checksum = evidence_api
    url = f"/api/v1/alerts/{identifier}"
    with Session(engine) as session:
        before = [
            session.scalar(select(func.count()).select_from(t))
            for t in (Alert, AnchorOutbox, Job, Run)
        ]
    ordinary = client.get(url + "/verify").json()
    assert ordinary["artifact_verification"] == "not_performed"
    response = client.get(url + "/verify", params={"verify_artifacts": "true"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["local_status"] == "local_match"
    assert result["artifact_verification"] == "verified"
    assert result["anchor_status"] == "unanchored"
    assert result["computed_hash"] == checksum
    assert client.get(url, params={"verify_artifacts": "true"}).json()["verification"] == result
    assert "storage_path" not in response.text
    with Session(engine) as session:
        record = session.get(Alert, identifier)
        assert (record.canonical_payload, record.original_hash) == (raw, checksum)
        after = [
            session.scalar(select(func.count()).select_from(t))
            for t in (Alert, AnchorOutbox, Job, Run)
        ]
        assert before == after
    assert client.get(url + "/verify", params={"verify_artifacts": "invalid"}).status_code == 422
    assert client.get(url + "/verify", params={"path": "private"}).status_code == 422


@pytest.mark.parametrize("missing", [False, True])
def test_source_tampering_or_missing_file_does_not_change_payload_status(evidence_api, missing):
    client, engine, _, identifier, raw, _ = evidence_api
    source_id = json.loads(raw)["source_checksums"][0]["asset_id"]
    from uuid import UUID

    with Session(engine) as session:
        path = Path(session.get(SourceAsset, UUID(source_id)).storage_path)
    if missing:
        path.unlink()
    else:
        path.write_bytes(b"tampered synthetic source copy")
    result = client.get(f"/api/v1/alerts/{identifier}/verify?verify_artifacts=true").json()
    assert result["local_status"] == "local_match"
    assert result["artifact_verification"] == ("partial" if missing else "mismatch")
    assert any(
        item["reason"] == ("missing_file" if missing else "checksum_mismatch")
        for item in result["artifact_audit"]["items"]
    )


def test_verification_repositories_cannot_write(evidence_api, monkeypatch):
    client, engine, _, identifier, _, _ = evidence_api
    from sqlalchemy import text

    def forbidden(session, *args):
        session.execute(text("UPDATE regions SET description='must never commit'"))

    monkeypatch.setattr("firewatch.api.presentation.audit_artifacts", forbidden)
    response = client.get(f"/api/v1/alerts/{identifier}/verify?verify_artifacts=true")
    assert response.status_code == 503
    with Session(engine) as session:
        assert (
            session.scalar(
                text("SELECT count(*) FROM regions WHERE description='must never commit'")
            )
            == 0
        )


@pytest.mark.parametrize(
    "state", [AnchorStatus.QUEUED, AnchorStatus.SUBMITTED, AnchorStatus.FAILED]
)
def test_nonconfirmed_receipts_never_claim_anchor_match(evidence_api, state):
    client, engine, settings, identifier, _, checksum = evidence_api
    with Session(engine) as session, session.begin():
        session.add(
            Anchor(
                alert_id=identifier,
                chain_id=31337,
                contract_address="0x" + "a" * 40,
                transaction_hash="0x" + "b" * 64,
                anchored_digest=checksum,
                confirmation_status=state,
                network_kind="local",
            )
        )
    settings.chain_enabled = True

    class Reader:
        def read_digest(self, anchor):
            pytest.fail("Unconfirmed receipt must not be treated as a confirmed anchor")

    client.app.state.anchor_reader = Reader()
    response = client.get(f"/api/v1/alerts/{identifier}/verify").json()
    assert response["anchor_status"] == (
        "anchor_unavailable" if state == AnchorStatus.FAILED else "anchor_pending"
    )


def test_adapter_outage_is_unavailable_and_local_result_survives(evidence_api):
    client, engine, settings, identifier, _, checksum = evidence_api
    with Session(engine) as session, session.begin():
        session.add(
            Anchor(
                alert_id=identifier,
                chain_id=31337,
                contract_address="0x" + "a" * 40,
                transaction_hash="0x" + "b" * 64,
                anchored_digest=checksum,
                confirmation_status=AnchorStatus.CONFIRMED,
                network_kind="local",
            )
        )
    settings.chain_enabled = True

    class Reader:
        def read_digest(self, anchor):
            raise TimeoutError("PRIVATE_RPC_SECRET")

    client.app.state.anchor_reader = Reader()
    response = client.get(f"/api/v1/alerts/{identifier}/verify?verify_artifacts=true")
    result = response.json()
    assert result["local_status"] == "local_match"
    assert result["artifact_verification"] == "verified"
    assert result["anchor_status"] == "anchor_unavailable"
    assert "PRIVATE_RPC_SECRET" not in response.text


def test_cli_tampered_copy_only_changes_copy_result(evidence_api, tmp_path, monkeypatch):
    _, engine, config, identifier, raw, checksum = evidence_api
    from firewatch import cli

    class LocalDatabase:
        def __init__(self, settings):
            pass

        @contextmanager
        def session(self):
            with Session(engine) as session:
                yield session

        def close(self):
            pass

    monkeypatch.setattr(cli, "get_settings", lambda: config)
    monkeypatch.setattr(cli, "Database", LocalDatabase)
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        runner = CliRunner()
        good = runner.invoke(
            cli.app, ["verify-alert", "--alert-id", str(identifier), "--artifacts"]
        )
        assert good.exit_code == 0, good.output
        copy = tmp_path / "copy.json"
        value = json.loads(raw)
        value["priority_score"] = "999"
        copy.write_text(json.dumps(value))
        result = runner.invoke(
            cli.app, ["verify-alert", "--alert-id", str(identifier), "--payload-copy", str(copy)]
        )
        assert result.exit_code == 1, result.output
        output = json.loads(result.output)
        assert output["local_status"] == "local_match"
        assert output["copied_payload"]["local_status"] == "local_mismatch"
        invalid = runner.invoke(cli.app, ["verify-alert", "--alert-id", "bad"])
        assert invalid.exit_code == 2 and "postgresql" not in invalid.output
        assert "SET TRANSACTION READ ONLY" in statements
        assert not any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    with Session(engine) as session:
        record = session.get(Alert, identifier)
        assert (record.canonical_payload, record.original_hash) == (raw, checksum)
