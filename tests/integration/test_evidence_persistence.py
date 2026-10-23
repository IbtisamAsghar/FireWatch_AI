"""Transactional evidence/outbox publication on migrated disposable schemas."""

import hashlib
import json

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_worker import CLOCK, take, work  # noqa: F401

from firewatch.db.models import Alert, AnchorOutbox, Job, Run
from firewatch.evidence.serialization import CANONICALIZATION_VERSION
from firewatch.pipeline.runner import execute_run
from firewatch.schemas.common import Stage
from firewatch.sources.firms_normalize import digest

pytestmark = pytest.mark.integration


def chain(config):
    from pydantic import SecretStr

    config.chain_enabled = True
    config.alert_registry_address = "0x" + "1" * 40
    config.chain_private_key = SecretStr("1" * 64)  # Synthetic; no transaction signing.


def test_formal_evidence_is_stored_once_and_api_reads_exact_hash(work, monkeypatch):  # noqa: F811
    engine, config, _, _ = work
    import firewatch.pipeline.runner as runner

    real = runner.evidence_content
    calls = []

    def counted(payload):
        result = real(payload)
        calls.append(result)
        return result

    monkeypatch.setattr(runner, "evidence_content", counted)
    lease = take(engine, config)
    run_id = execute_run(engine, config, lease, lambda: CLOCK)
    assert execute_run(engine, config, lease, lambda: CLOCK) == run_id
    with Session(engine) as session:
        alert = session.scalar(select(Alert).where(Alert.run_id == run_id))
        assert calls == [(alert.canonical_payload, alert.original_hash)]
        payload = json.loads(alert.canonical_payload)
        assert payload["canonicalization_version"] == CANONICALIZATION_VERSION
        assert payload["priority_score"] == "150"
        assert payload["region"]["region_id"] == str(session.get(Run, run_id).region_id)
        assert hashlib.sha256(alert.canonical_payload).hexdigest() == alert.original_hash
        assert session.scalar(select(func.count()).select_from(AnchorOutbox)) == 0


def test_outbox_failure_rolls_back_evidence_and_checkpoint_then_retries(work):  # noqa: F811
    engine, config, identifier, _ = work
    chain(config)
    lease = take(engine, config)

    def fail(*args):
        raise RuntimeError("synthetic outbox failure")

    event.listen(AnchorOutbox, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="synthetic outbox"):
            execute_run(engine, config, lease, lambda: CLOCK)
    finally:
        event.remove(AnchorOutbox, "before_insert", fail)
    with Session(engine) as session:
        run = session.get(Run, session.get(Job, identifier).result_run_id)
        assert run.stage_status[Stage.SCORING.value] == "succeeded"
        assert run.stage_status.get(Stage.EVIDENCE.value) != "succeeded"
        assert session.scalar(select(func.count()).select_from(Alert)) == 0
        assert session.scalar(select(func.count()).select_from(AnchorOutbox)) == 0
    run_id = execute_run(engine, config, lease, lambda: CLOCK)
    with Session(engine) as session:
        assert session.get(Run, run_id).status == "completed"
        assert session.scalar(select(func.count()).select_from(Alert)) == 1
        assert session.scalar(select(func.count()).select_from(AnchorOutbox)) == 1


def test_hash_is_chain_independent_and_network_is_pinned_before_resume(work):  # noqa: F811
    engine, config, identifier, _ = work
    chain(config)
    lease = take(engine, config)

    def stop(stage):
        if stage == Stage.SCORING:
            raise SystemExit("scoring committed")

    with pytest.raises(SystemExit):
        execute_run(engine, config, lease, lambda: CLOCK, after_stage=stop)
    # Chain/RPC may now be unavailable. No chain adapter is consulted by evidence.
    config.chain_enabled = False
    config.chain_rpc_url = "http://127.0.0.1:1"
    run_id = execute_run(engine, config, lease, lambda: CLOCK)
    with Session(engine) as session:
        alert = session.scalar(select(Alert).where(Alert.run_id == run_id))
        outbox = session.scalar(select(AnchorOutbox).where(AnchorOutbox.alert_id == alert.id))
        assert outbox.network == "eip155:31337:" + "0x" + "1" * 40
        assert outbox.status == "queued" and outbox.tx_hash is None
        assert (
            b"chain_rpc" not in alert.canonical_payload
            and b"private_key" not in alert.canonical_payload
        )
        with pytest.raises(IntegrityError), session.begin_nested():
            alert.canonical_payload = b"tampered"
            alert.original_hash = hashlib.sha256(b"tampered").hexdigest()
            session.flush()


def test_prephase_run_resumes_legacy_bytes_and_stays_immutable(work):  # noqa: F811
    engine, config, identifier, _ = work
    lease = take(engine, config)

    def stop(stage):
        if stage == Stage.SCORING:
            raise SystemExit("scoring committed")

    with pytest.raises(SystemExit):
        execute_run(engine, config, lease, lambda: CLOCK, after_stage=stop)
    # Simulate an already-admitted pre-5.1 run using its genuine frozen format.
    with Session(engine) as session, session.begin():
        run = session.get(Run, session.get(Job, identifier).result_run_id)
        manifest = dict(run.input_asset_manifest)
        frozen = dict(manifest["orchestration"])
        frozen.pop("evidence_canonicalization")
        frozen.pop("region")
        manifest["orchestration"] = frozen
        run.input_asset_manifest = manifest
        run.config_hash = digest(frozen)
    run_id = execute_run(engine, config, lease, lambda: CLOCK)
    with Session(engine) as session:
        alert = session.scalar(select(Alert).where(Alert.run_id == run_id))
        original = (alert.canonical_payload, alert.original_hash)
        payload = json.loads(original[0])
        assert "canonicalization_version" not in payload
        assert payload["priority_score"] == 150.0
    assert execute_run(engine, config, lease, lambda: CLOCK) == run_id
    with Session(engine) as session:
        alert = session.scalar(select(Alert).where(Alert.run_id == run_id))
        assert (alert.canonical_payload, alert.original_hash) == original
