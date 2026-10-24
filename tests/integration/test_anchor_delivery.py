"""Durable anchor delivery on migrated PostgreSQL with deterministic chain faults."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from conftest import migrate
from eth_utils import keccak
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_read_api import seed

from firewatch.config import Settings
from firewatch.db.models import Alert, Anchor, AnchorOutbox, Job, Run
from firewatch.evidence.anchor_delivery import network, process_one
from firewatch.evidence.chain_rpc import ChainError, TransactionSigner
from firewatch.schemas.common import AnchorStatus, JobStatus, JobType

pytestmark = pytest.mark.integration
CLOCK = datetime(2026, 10, 24, tzinfo=UTC)


class FakeRPC:
    def __init__(self, config, expected):
        self.settings, self.address = config, config.alert_registry_address.lower()
        self.signer = TransactionSigner(config).address
        self.expected = expected
        self.observed = "0x" + "0" * 64
        self.sent = []
        self.saved_receipt = None
        self.head, self.nonce, self.balance = 1, 0, 10**20
        self.gas_price = 10**9
        self.auto_mine, self.canonical_block = True, True
        self.error, self.after_send, self.on_send = None, False, lambda _: None

    def identity(self):
        if self.error:
            raise ChainError(self.error)

    def writer(self):
        return self.signer

    def digest(self, key, block="latest"):
        return self.observed

    def receipt(self, tx_hash):
        return self.saved_receipt

    def canonical(self, receipt):
        return self.canonical_block, self.head

    def call(self, method, params):
        if method == "eth_getTransactionCount":
            return hex(self.nonce)
        if method == "eth_gasPrice":
            return hex(self.gas_price)
        if method == "eth_getBalance":
            return hex(self.balance)
        if method == "eth_getTransactionByHash":
            return {"hash": params[0]} if self.sent else None
        if method == "eth_sendRawTransaction":
            self.on_send(params[0])
            tx_hash = "0x" + keccak(bytes.fromhex(params[0][2:])).hex()
            self.sent.append(params[0])
            if self.auto_mine:
                self.nonce = 1
                self.observed = self.expected
                self.saved_receipt = dict(
                    transactionHash=tx_hash,
                    to=self.address,
                    **{"from": self.signer},
                    blockHash="0x" + "ab" * 32,
                    blockNumber="0x1",
                    status="0x1",
                )
            if self.after_send:
                raise ChainError("CHAIN_RPC_UNAVAILABLE")
            return tx_hash
        raise AssertionError(method)


@pytest.fixture
def case(empty_engine):
    migrate(empty_engine)
    ids = seed(empty_engine)
    config = Settings(
        database_url="postgresql+psycopg://test:password@localhost:1/firewatch_test",
        admin_api_token="test-only-" + "x" * 40,
        chain_enabled=True,
        chain_private_key=SecretStr("1" * 64),
        alert_registry_address="0x" + "22" * 20,
        chain_poll_seconds=1,
        chain_recheck_seconds=1,
        _env_file=None,
    )
    with Session(empty_engine, expire_on_commit=False) as session, session.begin():
        alert = session.get(Alert, ids["alert"])
        outbox = AnchorOutbox(alert_id=alert.id, network=network(config), available_at=CLOCK)
        session.add(outbox)
        session.flush()
        job = Job(
            type=JobType.ANCHOR,
            payload={"outbox_id": str(outbox.id)},
            payload_hash="a" * 64,
            idempotency_key="synthetic-anchor",
        )
        session.add(job)
        expected = "0x" + alert.original_hash
        original = alert.canonical_payload, alert.original_hash
    return empty_engine, config, outbox.id, FakeRPC(config, expected), original


def tick(case, seconds=0, **kwargs):
    engine, config, _, rpc, _ = case
    return process_one(
        engine, config, rpc=rpc, now=lambda: CLOCK + timedelta(seconds=seconds), **kwargs
    )


def state(case):
    with Session(case[0]) as session:
        row = session.get(AnchorOutbox, case[2])
        session.expunge(row)
        return row


def test_durable_hash_precedes_broadcast_receipt_and_job_complete(case):
    engine, config, identifier, rpc, original = case

    def before_send(raw):
        row = state(case)
        assert row.status == AnchorStatus.SUBMITTED and row.tx_hash
        assert row.receipt_metadata["raw_transaction"] == raw

    rpc.on_send = before_send
    assert tick(case)
    assert state(case).status == AnchorStatus.SUBMITTED
    assert tick(case, 1)
    assert state(case).status == AnchorStatus.SUBMITTED  # One confirmation is insufficient.
    rpc.head = 2
    assert tick(case, 2)
    assert state(case).status == AnchorStatus.CONFIRMED
    with Session(engine) as session:
        anchor = session.scalar(select(Anchor))
        assert anchor.block_height == 1 and anchor.network_kind == "local"
        assert anchor.confirmation_status == AnchorStatus.CONFIRMED
        assert (
            session.scalar(select(Job).where(Job.type == JobType.ANCHOR)).status
            == JobStatus.SUCCEEDED
        )
        alert = session.get(Alert, state(case).alert_id)
        assert (alert.canonical_payload, alert.original_hash) == original
        assert session.scalar(select(func.count()).select_from(Run)) == 4
    assert len(rpc.sent) == 1


@pytest.mark.parametrize("stage", ["prepared", "broadcast", "confirmed"])
def test_crash_at_every_transaction_boundary_never_sends_a_new_identity(case, stage):
    rpc = case[3]
    rpc.head = 2

    def crash(actual):
        if actual == stage:
            raise SystemExit("synthetic signer crash")

    if stage == "confirmed":
        tick(case)
    with pytest.raises(SystemExit):
        tick(case, 2 if stage == "confirmed" else 0, checkpoint=crash)
    saved_hash = state(case).tx_hash
    tick(case, 10)
    tick(case, 20)
    assert state(case).status == AnchorStatus.CONFIRMED
    assert state(case).tx_hash == saved_hash and len(rpc.sent) == 1


def test_accepted_broadcast_with_timeout_recovers_from_receipt(case):
    rpc = case[3]
    rpc.after_send, rpc.head = True, 2
    tick(case)
    assert state(case).error == "CHAIN_RPC_UNAVAILABLE" and state(case).tx_hash
    tick(case, 10)
    assert state(case).status == AnchorStatus.CONFIRMED and len(rpc.sent) == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        ("funds", "CHAIN_INSUFFICIENT_FUNDS"),
        ("fees", "CHAIN_FEE_LIMIT"),
        ("writer", "CHAIN_WRITER_MISMATCH"),
        ("conflict", "CHAIN_DIGEST_CONFLICT"),
    ],
)
def test_distinct_delivery_failures_do_not_touch_saved_evidence(case, failure, code):
    rpc = case[3]
    if failure == "funds":
        rpc.balance = 0
    elif failure == "fees":
        rpc.gas_price = 10**20
    elif failure == "writer":
        rpc.signer = "0x" + "33" * 20
    elif failure == "conflict":
        rpc.observed = "0x" + "ff" * 32
    tick(case)
    assert state(case).error == code and not rpc.sent
    assert state(case).status == (
        AnchorStatus.QUEUED if failure in {"funds", "fees"} else AnchorStatus.FAILED
    )
    with Session(case[0]) as session:
        record = session.get(Alert, state(case).alert_id)
        assert (record.canonical_payload, record.original_hash) == case[4]


def test_bounded_unsent_retries_and_recovery_after_funding(case):
    case[1].chain_max_attempts = 2
    rpc = case[3]
    rpc.balance = 0
    tick(case)
    rpc.balance = 10**20
    tick(case, 10)
    assert state(case).status == AnchorStatus.SUBMITTED
    assert len(rpc.sent) == 1


def test_rpc_outage_without_signed_transaction_stops_at_attempt_limit(case):
    case[1].chain_max_attempts = 2
    case[3].error = "CHAIN_RPC_UNAVAILABLE"
    tick(case)
    tick(case, 10)
    assert state(case).status == AnchorStatus.FAILED and state(case).tx_hash is None


def test_reverted_receipt_is_failed_and_nonce_consumption_recorded(case):
    tick(case)
    case[3].saved_receipt["status"] = "0x0"
    tick(case, 2)
    row = state(case)
    assert row.status == AnchorStatus.FAILED and row.error == "CHAIN_TRANSACTION_REVERTED"
    assert row.receipt_metadata["nonce_consumed"] is True


def test_reorg_demotes_confirmed_anchor_and_job(case):
    case[3].head = 2
    tick(case)
    tick(case, 2)
    assert state(case).status == AnchorStatus.CONFIRMED
    case[3].canonical_block = False
    tick(case, 5)
    assert state(case).status == AnchorStatus.SUBMITTED and state(case).error == "CHAIN_REORG"
    with Session(case[0]) as session:
        assert session.scalar(select(Anchor)).confirmation_status == AnchorStatus.SUBMITTED
        assert (
            session.scalar(select(Job).where(Job.type == JobType.ANCHOR)).status
            == JobStatus.RUNNING
        )


def test_corrupt_saved_signed_transaction_is_never_broadcast(case):
    def crash(stage):
        if stage == "prepared":
            raise SystemExit()

    with pytest.raises(SystemExit):
        tick(case, checkpoint=crash)
    with Session(case[0]) as session, session.begin():
        row = session.get(AnchorOutbox, case[2])
        row.receipt_metadata = dict(row.receipt_metadata) | {"nonce": 99}
    tick(case, 10)
    assert state(case).error == "CHAIN_DELIVERY_CORRUPT" and not case[3].sent


def test_signer_lock_excludes_concurrent_worker_processes(case):
    entered, release = Event(), Event()
    original = case[3].identity

    def wait():
        entered.set()
        assert release.wait(10)
        original()

    case[3].identity = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(tick, case)
        assert entered.wait(5)
        try:
            assert pool.submit(tick, case).result(timeout=5) is False
        finally:
            release.set()
        assert first.result(timeout=5)
    assert len(case[3].sent) == 1


def test_chain_disabled_does_not_construct_signer_or_call_rpc(case):
    case[1].chain_enabled = False
    assert tick(case) is False and not case[3].sent


def test_read_only_api_configuration_needs_no_signing_key(case):
    from firewatch.evidence.chain_rpc import TransactionSigner

    value = case[1].model_dump()
    value["chain_private_key"] = None
    config = Settings(**value, _env_file=None)
    with pytest.raises(ChainError, match="CHAIN_SIGNER_UNAVAILABLE"):
        TransactionSigner(config)


def test_pending_transaction_blocks_new_nonce_without_consuming_failure_budget(case):
    from test_schema import alert, outputs, running

    from firewatch.repositories.runs import complete_and_publish_run

    rpc = case[3]
    rpc.auto_mine = False
    tick(case)
    with Session(case[0], expire_on_commit=False) as session, session.begin():
        run = running(session)
        _, score = outputs(session, run)
        evidence = alert(session, score)
        complete_and_publish_run(session, run.id, CLOCK)
        row = AnchorOutbox(alert_id=evidence.id, network=network(case[1]), available_at=CLOCK)
        session.add(row)
        session.flush()
        identifier = row.id
    tick(case, 10)  # New row is older than the first row's rescheduled receipt poll.
    with Session(case[0]) as session:
        row = session.get(AnchorOutbox, identifier)
        assert row.error == "CHAIN_SIGNER_BUSY" and row.tx_hash is None
        assert row.receipt_metadata["consecutive_errors"] == 0
    assert len(rpc.sent) == 1


def test_cli_status_and_safe_retry_preserve_transaction_identity(case, monkeypatch):
    from contextlib import contextmanager

    from typer.testing import CliRunner

    from firewatch import cli

    class LocalDatabase:
        def __init__(self, settings):
            self.engine = case[0]

        @contextmanager
        def session(self):
            with Session(self.engine) as session:
                yield session

        def close(self):
            pass

    monkeypatch.setattr(cli, "get_settings", lambda: case[1])
    monkeypatch.setattr(cli, "Database", LocalDatabase)
    case[1].chain_max_attempts = 1
    case[3].balance = 0
    tick(case)
    assert state(case).status == AnchorStatus.FAILED
    runner = CliRunner()
    with Session(case[0]) as session:
        job = session.scalar(select(Job).where(Job.type == JobType.ANCHOR))
        job_id = job.id
    result = runner.invoke(cli.app, ["anchor-status", "--job-id", str(job_id)])
    assert result.exit_code == 0, result.output
    assert "CHAIN_INSUFFICIENT_FUNDS" in result.output
    assert "raw_transaction" not in result.output and "private_key" not in result.output
    retry = runner.invoke(cli.app, ["retry-anchor", "--outbox-id", str(case[2])])
    assert retry.exit_code == 0, retry.output
    assert state(case).status == AnchorStatus.QUEUED and state(case).tx_hash is None
    # Known reverted transactions must not be blindly requeued with a fresh nonce.
    with Session(case[0]) as session, session.begin():
        row = session.get(AnchorOutbox, case[2])
        row.status = AnchorStatus.FAILED
        row.receipt_metadata = {"nonce_consumed": True}
    refused = runner.invoke(cli.app, ["retry-anchor", "--outbox-id", str(case[2])])
    assert refused.exit_code == 2


def test_anchor_backlog_does_not_consume_pipeline_admission_or_scheduler_capacity(api):
    from test_operator_api import post, prepare, refresh_body

    from firewatch.pipeline.jobs import schedule

    client, engine, settings = api
    area, _ = prepare(engine)
    settings.max_job_queue_depth = 1
    with Session(engine) as session, session.begin():
        session.add(
            Job(
                type=JobType.ANCHOR,
                payload={},
                payload_hash="a" * 64,
                idempotency_key="synthetic-stalled-chain",
                status=JobStatus.RUNNING,
            )
        )
    response = post(client, "jobs/refresh", refresh_body(area))
    assert response.status_code == 202, response.text
    with Session(engine) as session, session.begin():
        pending = session.scalar(select(Job).where(Job.type == JobType.REFRESH))
        pending.status = JobStatus.SUCCEEDED
    with Session(engine) as session, session.begin():
        assert schedule(session, settings, CLOCK) == 1
