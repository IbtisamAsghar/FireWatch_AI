"""Real loopback Anvil + disposable PostgreSQL acceptance; no application data or keys."""

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from eth_account import Account
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.models import Alert, AnchorOutbox, Job, Run
from firewatch.db.session import get_session
from firewatch.evidence.anchor_delivery import process_one
from firewatch.main import create_app
from scripts.contract_tools import FLAGS, ROOT, broadcast, rpc, tools
from scripts.demo_api import demo_schema
from scripts.demo_data import IDS


def crash_child() -> int:
    data = json.load(sys.stdin)
    config = Settings(**data["settings"], _env_file=None)
    schema = data["schema"]
    if (
        not (make_url(config.database_url.get_secret_value()).database or "").endswith("_test")
        or config.app_env != "test"
        or config.chain_id != 31337
        or not re.fullmatch(r"fw_demo_[0-9a-f]{32}", schema)
    ):
        raise ValueError("Invalid disposable test target")
    engine = create_engine(
        config.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )

    def crash(stage):
        if stage == "prepared":
            os._exit(23)  # Abrupt process death: no Python cleanup or advisory unlock.

    try:
        process_one(
            engine, config, now=lambda: datetime.fromisoformat(data["now"]), checkpoint=crash
        )
        return 1
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, default=ROOT / "artifacts/phase-5.4-smoke.json")
    parser.add_argument("--crash-prepared", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.crash_prepared:
        return crash_child()
    # Never read the application's .env or use its database as a fallback.
    url = os.environ.get("FIREWATCH_TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("Set FIREWATCH_TEST_DATABASE_URL to a disposable _test database")
    config = Settings(
        database_url=url, admin_api_token=secrets.token_urlsafe(40), app_env="test", _env_file=None
    )
    paths = tools()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    rpc_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            paths["anvil"],
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--chain-id",
            "31337",
            "--accounts",
            "0",
            "--silent",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=FLAGS,
    )
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                if rpc(rpc_url, "eth_chainId", []) == "0x7a69":
                    break
            except OSError:
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Local Anvil unavailable")
            time.sleep(0.1)
        wallet = Account.create()
        private_key = "0x" + bytes(wallet.key).hex()
        rpc(rpc_url, "anvil_setBalance", [wallet.address, hex(10**20)])
        env = dict(
            os.environ,
            FIREWATCH_DEPLOY_PRIVATE_KEY=private_key,
            FIREWATCH_DEPLOY_CHAIN_ID="31337",
            FIREWATCH_REGISTRY_WRITER=wallet.address,
        )
        deployment = broadcast(paths, "DeployAlertRegistry", rpc_url, env)
        address = deployment["transactions"][0]["contractAddress"]
        config = config.model_copy(
            update=dict(
                chain_enabled=True,
                chain_rpc_url=SecretStr(rpc_url),
                alert_registry_address=address,
                chain_private_key=SecretStr(private_key),
                chain_poll_seconds=1,
                chain_recheck_seconds=1,
            )
        )
        clock = datetime.now(UTC)
        with demo_schema(config) as engine:
            with Session(engine) as session:
                originals = {
                    a.id: (a.canonical_payload, a.original_hash)
                    for a in session.scalars(select(Alert))
                }
                before_runs = session.scalar(select(func.count()).select_from(Run))
            app = create_app(config)

            def sessions():
                with Session(engine) as session:
                    yield session

            app.dependency_overrides[get_session] = sessions
            with TestClient(app) as client:
                headers = {
                    "Authorization": "Bearer " + config.admin_api_token.get_secret_value(),
                    "Idempotency-Key": "phase54-local-smoke",
                }
                response = client.post(
                    f"/api/v1/alerts/{IDS['alert']}/anchor", json={}, headers=headers
                )
                assert response.status_code == 202, "Operator anchor admission failed"
                job_id = response.json()["job_id"]
                clock = datetime.now(UTC)
                with Session(engine) as session:
                    outbox_id = session.scalar(select(AnchorOutbox.id))
                snapshot = rpc(rpc_url, "evm_snapshot", [])

                with engine.connect() as connection:
                    schema = connection.scalar(text("SELECT current_schema()"))
                child_settings = config.model_dump(mode="json")
                child_settings.update(
                    database_url=config.database_url.get_secret_value(),
                    admin_api_token=config.admin_api_token.get_secret_value(),
                    chain_rpc_url=rpc_url,
                    chain_private_key=private_key,
                )
                crashed = subprocess.run(
                    [sys.executable, "-m", "scripts.verify_anchoring", "--crash-prepared"],
                    input=json.dumps(
                        dict(
                            settings=child_settings,
                            schema=schema,
                            now=(clock + timedelta(seconds=1)).isoformat(),
                        )
                    ),
                    text=True,
                    capture_output=True,
                    cwd=ROOT,
                    timeout=30,
                    creationflags=FLAGS,
                )
                assert crashed.returncode == 23, "Expected abrupt post-commit signer exit"
                with Session(engine) as session:
                    row = session.get(AnchorOutbox, outbox_id)
                    tx_hash = row.tx_hash
                    assert row.receipt_metadata["raw_transaction"] and tx_hash
                assert rpc(rpc_url, "eth_getTransactionReceipt", [tx_hash]) is None
                process_one(engine, config, now=lambda: clock + timedelta(seconds=3))
                process_one(engine, config, now=lambda: clock + timedelta(seconds=5))
                verify_url = f"/api/v1/alerts/{IDS['alert']}/verify"
                assert client.get(verify_url).json()["anchor_status"] == "anchor_pending"
                rpc(rpc_url, "evm_mine", [])
                process_one(engine, config, now=lambda: clock + timedelta(seconds=7))
                verified = client.get(verify_url).json()
                assert verified["anchor_status"] == "anchor_match"
                assert verified["local_status"] == "local_match"
                assert (
                    client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()["status"]
                    == "succeeded"
                )
                assert rpc(rpc_url, "evm_revert", [snapshot]) is True
                assert client.get(verify_url).json()["anchor_status"] == "anchor_unavailable"
                # Reorg recovery keeps the already-signed transaction identity and nonce.
                for index in range(5):
                    process_one(
                        engine, config, now=lambda i=index: clock + timedelta(seconds=10 + i * 3)
                    )
                    rpc(rpc_url, "evm_mine", [])
                recovered = client.get(verify_url).json()
                assert recovered["anchor_status"] == "anchor_match", "Reorg recovery failed"
                with Session(engine) as session:
                    row = session.get(AnchorOutbox, outbox_id)
                    assert row.tx_hash == tx_hash
                    assert row.status.value == "confirmed"
                    assert originals == {
                        a.id: (a.canonical_payload, a.original_hash)
                        for a in session.scalars(select(Alert))
                    }
                    assert session.scalar(select(func.count()).select_from(Run)) == before_runs
                    assert session.scalar(select(func.count()).select_from(Job)) == 2
                # An outage changes chain availability while the local evidence still matches.
                config.chain_rpc_url = SecretStr("http://127.0.0.1:1")
                unavailable = client.get(verify_url).json()
                assert unavailable["anchor_status"] == "anchor_unavailable"
                assert unavailable["local_status"] == "local_match"
                assert client.get("/health/live").status_code == 200
                config.chain_rpc_url = SecretStr(rpc_url)
                assert private_key not in json.dumps(recovered)
        # Check generated Foundry outputs for the ephemeral key before saving any receipt.
        key_forms = [
            private_key.encode(),
            private_key[2:].encode(),
            str(int(private_key, 16)).encode(),
        ]
        for directory in ("broadcast", "cache", "out"):
            for file in (ROOT / "contracts" / directory).rglob("*"):
                if file.is_file():
                    raw = file.read_bytes()
                    assert not any(key in raw for key in key_forms)
        result = dict(
            schema_version="phase-5.4-anchoring-acceptance-v1",
            recorded_at=datetime.now(UTC).isoformat(),
            database_scope="disposable migrated PostgreSQL synthetic demo schema",
            network_kind="local",
            chain_id=31337,
            rpc_bind="127.0.0.1",
            contract_address=address,
            transaction_hash=tx_hash,
            dedicated_random_test_signer=True,
            abrupt_signer_exit_code=23,
            canonical_block_hash_reads=True,
            durable_prebroadcast_crash_recovery=True,
            confirmation_gate=True,
            operator_job_completed=True,
            real_chain_api_match=True,
            reorg_detected_and_recovered=True,
            same_transaction_identity_preserved=True,
            rpc_outage_distinct_from_mismatch=True,
            original_evidence_and_runs_unchanged=True,
            no_application_database_access=True,
            no_private_key_persisted=True,
            evidence_sha256=hashlib.sha256(originals[IDS["alert"]][0]).hexdigest(),
        )
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        print("PostgreSQL/Anvil durable delivery, HTTP verification and reorg recovery passed.")
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
