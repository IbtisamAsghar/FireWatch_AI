"""Bounded JSON-RPC, local signing and canonical receipt reads; no database writes."""

import json
import re
from typing import Any

import httpx

from firewatch.config import Settings
from firewatch.schemas.api import AnchorView

ANCHOR_SELECTOR = "0xa21f3c6a"
DIGEST_SELECTOR = "0x2de813da"
WRITER_SELECTOR = "0x453a2abc"


class ChainError(RuntimeError):
    def __init__(self, code: str, retryable: bool = True) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def quantity(value: Any) -> int:
    if not isinstance(value, str) or not re.fullmatch(
        r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]{0,63})", value
    ):
        raise ChainError("CHAIN_INVALID_RESPONSE")
    return int(value, 16)


def digest_hex(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{64}", value):
        raise ChainError("CHAIN_INVALID_RESPONSE")
    return value.lower()


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate RPC member")
        result[key] = value
    return result


class ChainRPC:
    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self.address = settings.alert_registry_address.lower()
        self.client = client or httpx.Client(
            timeout=settings.chain_rpc_timeout_seconds, trust_env=False, follow_redirects=False
        )

    def close(self) -> None:
        self.client.close()

    def call(self, method: str, params: list[Any]) -> Any:
        try:
            with self.client.stream(
                "POST",
                self.settings.chain_rpc_url.get_secret_value(),
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            ) as response:
                response.raise_for_status()
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        raise ChainError("CHAIN_INVALID_RESPONSE")
            document = json.loads(data, object_pairs_hook=unique)
            if (
                not isinstance(document, dict)
                or document.get("jsonrpc") != "2.0"
                or type(document.get("id")) is not int
                or document["id"] != 1
            ):
                raise ChainError("CHAIN_INVALID_RESPONSE")
            if "error" in document:
                message = str(document["error"].get("message", "")).lower()
                if "insufficient funds" in message:
                    raise ChainError("CHAIN_INSUFFICIENT_FUNDS")
                if "already known" in message or "known transaction" in message:
                    raise ChainError("CHAIN_TX_KNOWN")
                if "nonce too low" in message or "replacement transaction" in message:
                    raise ChainError("CHAIN_NONCE_CONFLICT", False)
                if "revert" in message:
                    raise ChainError("CHAIN_CALL_REVERTED", False)
                raise ChainError("CHAIN_RPC_REJECTED")
            return document["result"]
        except (httpx.HTTPError, OSError):
            raise ChainError("CHAIN_RPC_UNAVAILABLE") from None
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            raise ChainError("CHAIN_INVALID_RESPONSE") from None

    def identity(self) -> None:
        if quantity(self.call("eth_chainId", [])) != self.settings.chain_id:
            raise ChainError("CHAIN_ID_MISMATCH", False)
        code = self.call("eth_getCode", [self.address, "latest"])
        if not isinstance(code, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code):
            raise ChainError("CHAIN_CONTRACT_UNAVAILABLE")

    def digest(self, key: str, block: str | dict[str, Any] = "latest") -> str:
        return digest_hex(
            self.call("eth_call", [{"to": self.address, "data": DIGEST_SELECTOR + key[2:]}, block])
        )

    def writer(self) -> str:
        result = digest_hex(
            self.call("eth_call", [{"to": self.address, "data": WRITER_SELECTOR}, "latest"])
        )
        if result[2:26] != "0" * 24:
            raise ChainError("CHAIN_INVALID_RESPONSE")
        return "0x" + result[-40:]

    def receipt(self, tx_hash: str) -> dict[str, Any] | None:
        result = self.call("eth_getTransactionReceipt", [tx_hash])
        if result is None:
            return None
        if (
            not isinstance(result, dict)
            or digest_hex(result.get("transactionHash")) != tx_hash.lower()
            or str(result.get("to", "")).lower() != self.address
        ):
            raise ChainError("CHAIN_INVALID_RECEIPT")
        digest_hex(result.get("blockHash"))
        quantity(result.get("blockNumber"))
        if quantity(result.get("status")) not in {0, 1}:
            raise ChainError("CHAIN_INVALID_RECEIPT")
        return result

    def canonical(self, receipt: dict[str, Any]) -> tuple[bool, int]:
        height = quantity(receipt["blockNumber"])
        block = self.call("eth_getBlockByNumber", [hex(height), False])
        if block is None:
            return False, 0
        if not isinstance(block, dict) or quantity(block.get("number")) != height:
            raise ChainError("CHAIN_INVALID_RESPONSE")
        if digest_hex(block.get("hash")) != digest_hex(receipt["blockHash"]):
            return False, 0
        head = quantity(self.call("eth_blockNumber", []))
        return head >= height, max(0, head - height + 1)


class RpcAnchorReader:
    """Verification-only adapter: no key derivation, signing or transaction submission."""

    def __init__(self, settings: Settings, rpc: ChainRPC | None = None) -> None:
        self.rpc = rpc or ChainRPC(settings)

    def close(self) -> None:
        self.rpc.close()

    def read_digest(self, anchor: AnchorView) -> str:
        config = self.rpc.settings
        if (
            anchor.chain_id != config.chain_id
            or anchor.contract_address.lower() != self.rpc.address
            or anchor.network_kind != config.chain_network_kind
            or anchor.alert_key is None
        ):
            raise ChainError("CHAIN_DESTINATION_UNAVAILABLE")
        self.rpc.identity()
        receipt = self.rpc.receipt(anchor.transaction_hash)
        if receipt is None or quantity(receipt["status"]) != 1:
            raise ChainError("CHAIN_RECEIPT_UNAVAILABLE")
        if (
            digest_hex(receipt["blockHash"]) != digest_hex(anchor.block_hash)
            or quantity(receipt["blockNumber"]) != anchor.block_height
        ):
            raise ChainError("CHAIN_REORG")
        canonical, confirmations = self.rpc.canonical(receipt)
        if not canonical or confirmations < config.chain_confirmations:
            raise ChainError("CHAIN_CONFIRMATIONS_PENDING")
        return self.rpc.digest(
            anchor.alert_key,
            {"blockHash": digest_hex(receipt["blockHash"]), "requireCanonical": True},
        )


class TransactionSigner:
    """Private key is used only in the dedicated signer process, never RPC parameters."""

    def __init__(self, settings: Settings) -> None:
        from eth_account import Account

        if settings.chain_private_key is None:
            raise ChainError("CHAIN_SIGNER_UNAVAILABLE", False)
        try:
            self.account = Account.from_key(settings.chain_private_key.get_secret_value())
        except (ValueError, TypeError):
            raise ChainError("CHAIN_SIGNER_UNAVAILABLE", False) from None
        self.address = self.account.address.lower()

    def sign(self, transaction: dict[str, Any]) -> tuple[str, str]:
        try:
            signed = self.account.sign_transaction(transaction)
            return "0x" + signed.raw_transaction.hex(), "0x" + signed.hash.hex()
        except (ValueError, TypeError):
            raise ChainError("CHAIN_SIGNING_FAILED", False) from None
