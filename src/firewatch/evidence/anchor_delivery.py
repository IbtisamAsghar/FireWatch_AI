"""Crash-safe single-signer outbox delivery, independent of scoring and publication."""

import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.models import Alert, Anchor, AnchorOutbox, Job
from firewatch.evidence.anchor import alert_key, evidence_digest
from firewatch.evidence.chain_rpc import (
    ANCHOR_SELECTOR,
    ChainError,
    ChainRPC,
    TransactionSigner,
    digest_hex,
    quantity,
)
from firewatch.schemas.common import AnchorStatus, JobStatus, JobType


def network(settings: Settings) -> str:
    return f"eip155:{settings.chain_id}:{settings.alert_registry_address.lower()}"


def signer_lock(chain_id: int, address: str) -> int:
    return int.from_bytes(
        hashlib.sha256(
            f"firewatch-anchor-signer-v1:{chain_id}:{address.lower()}".encode()
        ).digest()[:8],
        "big",
        signed=True,
    )


@dataclass(frozen=True)
class Delivery:
    id: UUID
    alert_id: UUID
    payload: bytes
    original_hash: str
    tx_hash: str | None
    meta: dict[str, Any]


class OwnerLost(RuntimeError):
    pass


@contextmanager
def transaction(connection: Connection, pid: int) -> Iterator[Session]:
    with Session(connection, expire_on_commit=False) as session, session.begin():
        if session.scalar(select(func.pg_backend_pid())) != pid:
            raise OwnerLost("Signer connection changed")
        yield session


def sync_jobs(session: Session, row: AnchorOutbox) -> None:
    status = {
        AnchorStatus.QUEUED: JobStatus.QUEUED,
        AnchorStatus.SUBMITTED: JobStatus.RUNNING,
        AnchorStatus.CONFIRMED: JobStatus.SUCCEEDED,
        AnchorStatus.FAILED: JobStatus.FAILED,
    }[row.status]
    for job in session.scalars(
        select(Job).where(
            Job.type == JobType.ANCHOR, Job.payload["outbox_id"].astext == str(row.id)
        )
    ):
        job.status, job.attempts, job.error = status, row.attempts, row.error
        job.available_at = row.available_at
        job.lease_until = None


def save(
    connection: Connection,
    pid: int,
    delivery: Delivery,
    settings: Settings,
    now: datetime,
    status: AnchorStatus,
    meta: dict[str, Any],
    *,
    error: str | None = None,
    receipt: dict[str, Any] | None = None,
) -> None:
    with transaction(connection, pid) as session:
        row = session.get(AnchorOutbox, delivery.id)
        if row is None:
            raise OwnerLost("Outbox disappeared")
        row.status, row.error, row.receipt_metadata = status, error, meta
        if meta.get("transaction_hash"):
            row.tx_hash = meta["transaction_hash"]
        row.available_at = now + timedelta(
            seconds=(
                settings.chain_recheck_seconds
                if status == AnchorStatus.CONFIRMED
                else settings.chain_poll_seconds
            )
        )
        anchor = session.scalar(
            select(Anchor).where(
                Anchor.alert_id == row.alert_id,
                Anchor.chain_id == settings.chain_id,
                func.lower(Anchor.contract_address) == settings.alert_registry_address.lower(),
            )
        )
        if receipt is not None and row.tx_hash:
            if anchor is None:
                anchor = Anchor(
                    id=uuid5(row.id, "anchor-receipt-v1"),
                    alert_id=row.alert_id,
                    chain_id=settings.chain_id,
                    contract_address=settings.alert_registry_address.lower(),
                    transaction_hash=row.tx_hash,
                    anchored_digest=delivery.original_hash,
                    network_kind=settings.chain_network_kind,
                )
                session.add(anchor)
            anchor.confirmation_status = status
            anchor.block_hash = digest_hex(receipt["blockHash"])
            anchor.block_height = quantity(receipt["blockNumber"])
        elif anchor is not None:
            anchor.confirmation_status = status
            if error == "CHAIN_REORG":
                anchor.block_hash = None
                anchor.block_height = None
        sync_jobs(session, row)


def transaction_fields(
    settings: Settings, meta: dict[str, Any], key: str, digest: str
) -> dict[str, Any]:
    for name in ("nonce", "gas", "gas_price"):
        if type(meta.get(name)) is not int or not 0 <= meta[name] < 2**64:
            raise ChainError("CHAIN_DELIVERY_CORRUPT", False)
    if not 50000 <= meta["gas"] <= 500000 or meta["gas_price"] <= 0:
        raise ChainError("CHAIN_DELIVERY_CORRUPT", False)
    return dict(
        chainId=settings.chain_id,
        nonce=meta["nonce"],
        gas=meta["gas"],
        gasPrice=meta["gas_price"],
        to=bytes.fromhex(settings.alert_registry_address[2:]),
        value=0,
        data=ANCHOR_SELECTOR + key[2:] + digest[2:],
    )


def deliver(
    connection: Connection,
    pid: int,
    item: Delivery,
    settings: Settings,
    rpc: ChainRPC,
    signer: TransactionSigner,
    now: datetime,
    checkpoint: Callable[[str], None],
) -> None:
    try:
        key, digest = alert_key(item.alert_id), evidence_digest(item.payload, item.original_hash)
    except ValueError:
        raise ChainError("EVIDENCE_MISMATCH", False) from None
    rpc.identity()
    if rpc.writer() != signer.address:
        raise ChainError("CHAIN_WRITER_MISMATCH", False)
    meta = dict(item.meta)
    if item.tx_hash:
        if (
            meta.get("schema_version") != "anchor-delivery-v1"
            or meta.get("signer") != signer.address
            or meta.get("network") != network(settings)
            or meta.get("network_kind") != settings.chain_network_kind
            or meta.get("alert_key") != key
            or meta.get("evidence_digest") != digest
        ):
            raise ChainError("CHAIN_DELIVERY_CORRUPT", False)
        raw, expected = signer.sign(transaction_fields(settings, meta, key, digest))
        if raw != meta.get("raw_transaction") or expected != item.tx_hash:
            raise ChainError("CHAIN_DELIVERY_CORRUPT", False)
        receipt = rpc.receipt(item.tx_hash)
        if receipt is not None:
            canonical, confirmations = rpc.canonical(receipt)
            if not canonical:
                save(
                    connection,
                    pid,
                    item,
                    settings,
                    now,
                    AnchorStatus.SUBMITTED,
                    meta,
                    error="CHAIN_REORG",
                )
                return
            if str(receipt.get("from", "")).lower() != signer.address:
                raise ChainError("CHAIN_INVALID_RECEIPT", False)
            meta.update(
                block_hash=digest_hex(receipt["blockHash"]),
                block_height=quantity(receipt["blockNumber"]),
                confirmations=confirmations,
                consecutive_errors=0,
            )
            if quantity(receipt["status"]) == 0:
                meta["nonce_consumed"] = True
                save(
                    connection,
                    pid,
                    item,
                    settings,
                    now,
                    AnchorStatus.FAILED,
                    meta,
                    error="CHAIN_TRANSACTION_REVERTED",
                    receipt=receipt,
                )
                return
            if (
                rpc.digest(
                    key, {"blockHash": digest_hex(receipt["blockHash"]), "requireCanonical": True}
                )
                != digest
            ):
                raise ChainError("CHAIN_DIGEST_CONFLICT", False)
            status = (
                AnchorStatus.CONFIRMED
                if confirmations >= settings.chain_confirmations
                else AnchorStatus.SUBMITTED
            )
            save(connection, pid, item, settings, now, status, meta, receipt=receipt)
            checkpoint("confirmed" if status == AnchorStatus.CONFIRMED else "receipt")
            return
        if meta.get("block_hash"):
            meta["reorg_count"] = int(meta.get("reorg_count", 0)) + 1
            meta.pop("block_hash", None)
            meta.pop("block_height", None)
            save(
                connection,
                pid,
                item,
                settings,
                now,
                AnchorStatus.SUBMITTED,
                meta,
                error="CHAIN_REORG",
            )
    observed = rpc.digest(key)
    if observed not in {"0x" + "0" * 64, digest}:
        raise ChainError("CHAIN_DIGEST_CONFLICT", False)
    if not item.tx_hash:
        # Serialize nonces across all destinations using this chain/signer, including failed
        # ambiguous broadcasts. An uncertain transaction must never be replaced by new work.
        with transaction(connection, pid) as session:
            blockers = list(
                session.scalars(
                    select(AnchorOutbox).where(
                        AnchorOutbox.id != item.id,
                        AnchorOutbox.tx_hash.is_not(None),
                        AnchorOutbox.network.like(f"eip155:{settings.chain_id}:%"),
                        AnchorOutbox.status != AnchorStatus.CONFIRMED,
                    )
                )
            )
            if any(
                r.receipt_metadata.get("signer") == signer.address
                and not r.receipt_metadata.get("nonce_consumed")
                for r in blockers
            ):
                raise ChainError("CHAIN_SIGNER_BUSY")
        latest = quantity(rpc.call("eth_getTransactionCount", [signer.address, "latest"]))
        pending = quantity(rpc.call("eth_getTransactionCount", [signer.address, "pending"]))
        if pending != latest:
            raise ChainError("CHAIN_SIGNER_BUSY")
        gas_price = quantity(rpc.call("eth_gasPrice", []))
        if not 0 < gas_price <= settings.chain_max_gas_price_wei:
            raise ChainError("CHAIN_FEE_LIMIT")
        balance = quantity(rpc.call("eth_getBalance", [signer.address, "latest"]))
        if balance < gas_price * settings.chain_gas_limit:
            raise ChainError("CHAIN_INSUFFICIENT_FUNDS")
        meta.update(
            schema_version="anchor-delivery-v1",
            network=network(settings),
            network_kind=settings.chain_network_kind,
            signer=signer.address,
            nonce=latest,
            gas=settings.chain_gas_limit,
            gas_price=gas_price,
            alert_key=key,
            evidence_digest=digest,
            consecutive_errors=0,
        )
        raw, tx_hash = signer.sign(transaction_fields(settings, meta, key, digest))
        meta.update(raw_transaction=raw, transaction_hash=tx_hash, prepared_at=now.isoformat())
        save(connection, pid, item, settings, now, AnchorStatus.SUBMITTED, meta)
        checkpoint("prepared")  # Durable hash/raw/nonce commit must precede every broadcast.
    else:
        raw, tx_hash = meta["raw_transaction"], item.tx_hash
    pending_tx = rpc.call("eth_getTransactionByHash", [tx_hash])
    if pending_tx is not None:
        if not isinstance(pending_tx, dict) or digest_hex(pending_tx.get("hash")) != tx_hash:
            raise ChainError("CHAIN_INVALID_RESPONSE")
        save(connection, pid, item, settings, now, AnchorStatus.SUBMITTED, meta)
        return
    latest = quantity(rpc.call("eth_getTransactionCount", [signer.address, "latest"]))
    if latest > meta["nonce"]:
        raise ChainError("CHAIN_NONCE_CONFLICT", False)
    try:
        returned = rpc.call("eth_sendRawTransaction", [raw])
        if digest_hex(returned) != tx_hash:
            raise ChainError("CHAIN_TRANSACTION_HASH_MISMATCH", False)
    except ChainError as exc:
        if exc.code != "CHAIN_TX_KNOWN":
            raise
    checkpoint("broadcast")  # Crash here resumes the exact already-persisted transaction.
    meta.update(broadcast_at=now.isoformat(), consecutive_errors=0)
    save(connection, pid, item, settings, now, AnchorStatus.SUBMITTED, meta)


def process_one(
    engine: Engine,
    settings: Settings,
    *,
    rpc: ChainRPC | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    checkpoint: Callable[[str], None] = lambda _: None,
) -> bool:
    if not settings.chain_enabled:
        return False
    signer = TransactionSigner(settings)
    adapter = rpc or ChainRPC(settings)
    lock = signer_lock(settings.chain_id, signer.address)
    try:
        with engine.connect() as connection:
            acquired = connection.scalar(select(func.pg_try_advisory_lock(lock)))
            pid = connection.scalar(select(func.pg_backend_pid()))
            connection.commit()
            if not acquired or pid is None:
                return False
            try:
                clock = now()
                with transaction(connection, pid) as session:
                    row = session.scalar(
                        select(AnchorOutbox)
                        .where(
                            AnchorOutbox.network == network(settings),
                            AnchorOutbox.status != AnchorStatus.FAILED,
                            AnchorOutbox.available_at <= clock,
                        )
                        .order_by(AnchorOutbox.available_at, AnchorOutbox.id)
                        .limit(1)
                    )
                    if row is None:
                        return False
                    alert = session.get(Alert, row.alert_id)
                    if alert is None:
                        raise OwnerLost("Alert disappeared")
                    row.attempts += 1
                    item = Delivery(
                        row.id,
                        row.alert_id,
                        alert.canonical_payload,
                        alert.original_hash,
                        row.tx_hash,
                        dict(row.receipt_metadata),
                    )
                try:
                    deliver(connection, pid, item, settings, adapter, signer, clock, checkpoint)
                except ChainError as exc:
                    with transaction(connection, pid) as session:
                        row = session.get(AnchorOutbox, item.id)
                        if row is None:
                            raise OwnerLost("Outbox disappeared") from None
                        meta = dict(row.receipt_metadata)
                        errors = int(meta.get("consecutive_errors", 0)) + (
                            0 if exc.code == "CHAIN_SIGNER_BUSY" else 1
                        )
                        meta["consecutive_errors"] = errors
                        row.receipt_metadata, row.error = meta, exc.code
                        terminal = not exc.retryable or (
                            row.tx_hash is None and errors >= settings.chain_max_attempts
                        )
                        if terminal:
                            row.status = AnchorStatus.FAILED
                            anchor = session.scalar(
                                select(Anchor).where(
                                    Anchor.alert_id == row.alert_id,
                                    Anchor.chain_id == settings.chain_id,
                                    func.lower(Anchor.contract_address) == adapter.address,
                                )
                            )
                            if anchor:
                                anchor.confirmation_status = AnchorStatus.FAILED
                        row.available_at = clock + timedelta(
                            seconds=min(300, settings.chain_poll_seconds * 2 ** min(errors, 6))
                        )
                        sync_jobs(session, row)
                return True
            finally:
                try:
                    connection.rollback()
                    connection.execute(select(func.pg_advisory_unlock(lock)))
                    connection.commit()
                except Exception:
                    connection.invalidate()  # Closing the physical session releases its lock.
    finally:
        if rpc is None:
            adapter.close()
