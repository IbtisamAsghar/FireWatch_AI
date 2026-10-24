"""Bounded RPC errors, chain identity, receipt verification and signing isolation."""

import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from eth_account import Account
from pydantic import SecretStr

from firewatch.config import Settings
from firewatch.evidence.anchor import alert_key
from firewatch.evidence.chain_rpc import (
    ChainError,
    ChainRPC,
    RpcAnchorReader,
    TransactionSigner,
    digest_hex,
    quantity,
)
from firewatch.schemas.api import AnchorView
from firewatch.schemas.common import AnchorStatus


def config(**kwargs):
    return Settings(
        database_url="postgresql+psycopg://test:password@localhost:1/firewatch_test",
        admin_api_token="test-only-" + "x" * 40,
        alert_registry_address="0x" + "22" * 20,
        chain_private_key=SecretStr("1" * 64),
        _env_file=None,
        **kwargs,
    )


def rpc(handler, **kwargs):
    return ChainRPC(config(**kwargs), httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("value", [None, True, 1, "1", "0x", "0x01", "0x-1", "0x" + "f" * 65])
def test_invalid_rpc_quantities_rejected(value):
    with pytest.raises(ChainError):
        quantity(value)


@pytest.mark.parametrize("value", [None, "0x12", "f" * 64, "0x" + "g" * 64])
def test_invalid_digest_rejected(value):
    with pytest.raises(ChainError):
        digest_hex(value)


@pytest.mark.parametrize(
    "message,code",
    [
        ("insufficient funds PRIVATE_RPC_SECRET", "CHAIN_INSUFFICIENT_FUNDS"),
        ("already known", "CHAIN_TX_KNOWN"),
        ("nonce too low", "CHAIN_NONCE_CONFLICT"),
        ("execution reverted", "CHAIN_CALL_REVERTED"),
        ("PRIVATE_RPC_SECRET", "CHAIN_RPC_REJECTED"),
    ],
)
def test_rpc_errors_are_sanitized(message, code):
    adapter = rpc(
        lambda req: httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": message}}
        )
    )
    with pytest.raises(ChainError) as error:
        adapter.call("eth_blockNumber", [])
    assert error.value.code == code and "PRIVATE_RPC_SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "content",
    [
        b"not json",
        b'{"jsonrpc":"2.0","id":true,"result":"0x1"}',
        b'{"jsonrpc":"2.0","id":1,"id":1,"result":"0x1"}',
        b'{"jsonrpc":"2.0","id":2,"result":"0x1"}',
        b"x" * (1024 * 1024 + 1),
    ],
    ids=["invalid-json", "boolean-id", "duplicate-id", "wrong-id", "oversized"],
)
def test_malformed_or_oversized_rpc_is_unavailable(content):
    adapter = rpc(lambda req: httpx.Response(200, content=content))
    with pytest.raises(ChainError, match="CHAIN_INVALID_RESPONSE"):
        adapter.call("eth_blockNumber", [])


def test_transport_timeout_is_unavailable_without_secret_url():
    def timeout(request):
        raise httpx.ReadTimeout("PRIVATE_RPC_SECRET", request=request)

    with pytest.raises(ChainError, match="CHAIN_RPC_UNAVAILABLE") as error:
        rpc(timeout).call("eth_chainId", [])
    assert "PRIVATE_RPC_SECRET" not in str(error.value)


def test_signer_uses_direct_digest_and_reproducible_pinned_transaction():
    settings = config()
    settings.chain_private_key = SecretStr("0x" + bytes(Account.create().key).hex())
    signer = TransactionSigner(settings)
    transaction = dict(
        chainId=31337,
        nonce=0,
        gas=100000,
        gasPrice=10**9,
        to=bytes.fromhex("22" * 20),
        value=0,
        data="0xa21f3c6a" + "11" * 64,
    )
    raw, tx_hash = signer.sign(transaction)
    assert raw.startswith("0x") and len(tx_hash) == 66
    assert signer.sign(transaction) == (raw, tx_hash)
    assert Account.recover_transaction(raw).lower() == signer.address
    assert settings.chain_private_key.get_secret_value() not in raw


def test_reader_checks_destination_canonical_receipt_confirmations_and_saved_block():
    settings = config()
    tx_hash, block_hash = "0x" + "aa" * 32, "0x" + "bb" * 32
    expected = "0x" + hashlib.sha256(b"synthetic").hexdigest()
    head = 11
    calls = []

    def handler(request):
        nonlocal head
        document = json.loads(request.content)
        method, params = document["method"], document["params"]
        calls.append(method)
        values = {
            "eth_chainId": "0x7a69",
            "eth_getCode": "0x6000",
            "eth_getTransactionReceipt": dict(
                transactionHash=tx_hash,
                to=settings.alert_registry_address,
                blockHash=block_hash,
                blockNumber="0xa",
                status="0x1",
            ),
            "eth_getBlockByNumber": dict(hash=block_hash, number="0xa"),
            "eth_blockNumber": hex(head),
            "eth_call": expected,
        }
        if method == "eth_call":
            assert params[1] == {"blockHash": block_hash, "requireCanonical": True}
            assert params[0]["data"].startswith("0x2de813da")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": values[method]})

    adapter = ChainRPC(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    reader = RpcAnchorReader(settings, adapter)
    view = AnchorView(
        chain_id=31337,
        contract_address=settings.alert_registry_address,
        transaction_hash=tx_hash,
        block_hash=block_hash,
        block_height=10,
        confirmation_status=AnchorStatus.CONFIRMED,
        network_kind="local",
        alert_key=alert_key(uuid4()),
    )
    assert reader.read_digest(view) == expected
    assert "eth_sendRawTransaction" not in calls
    head = 10
    with pytest.raises(ChainError, match="CHAIN_CONFIRMATIONS_PENDING"):
        reader.read_digest(view)
    head = 11
    with pytest.raises(ChainError, match="CHAIN_REORG"):
        reader.read_digest(view.model_copy(update={"block_hash": "0x" + "cc" * 32}))
    with pytest.raises(ChainError, match="CHAIN_DESTINATION_UNAVAILABLE"):
        reader.read_digest(view.model_copy(update={"chain_id": 1}))
