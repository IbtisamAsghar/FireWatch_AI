"""Pinned native Foundry/Solidity installation, tests and isolated loopback Anvil smoke."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "contracts"
TOOL_HOME = ROOT / ".local" / "foundry"
PIN = json.loads((PROJECT / "toolchain.json").read_text(encoding="utf-8"))
FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def platform_key() -> str:
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Pinned native tools support x86_64 Windows and Linux")
    return {"win32": "win32-amd64", "linux": "linux-amd64"}[sys.platform]


def download(url: str, destination: Path, expected: str) -> None:
    if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == expected:
        return
    temp = destination.with_suffix(destination.suffix + ".partial")
    request = urllib.request.Request(url, headers={"User-Agent": "FireWatch-contract-tools"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as stream:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 256 * 1024 * 1024:
                    raise RuntimeError("Toolchain download exceeds bound")
                stream.write(chunk)
        if hashlib.sha256(temp.read_bytes()).hexdigest() != expected:
            raise RuntimeError("Official toolchain checksum mismatch")
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def install() -> None:
    pin = PIN["platforms"][platform_key()]
    TOOL_HOME.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    archive = TOOL_HOME / ("foundry.zip" if suffix else "foundry.tar.gz")
    download(pin["foundry_url"], archive, pin["foundry_sha256"])
    names = [name + suffix for name in ("forge", "cast", "anvil")]
    # Extract only exact expected flat regular files; never extract arbitrary archive paths.
    if suffix:
        with zipfile.ZipFile(archive) as zipped:
            for name in names:
                content = zipped.read(name)
                (TOOL_HOME / name).write_bytes(content)
    else:
        with tarfile.open(archive) as packed:
            for name in names:
                member = packed.getmember(name)
                if not member.isfile():
                    raise RuntimeError("Toolchain member is not a regular file")
                stream = packed.extractfile(member)
                if stream is None:
                    raise RuntimeError("Toolchain member is missing")
                with stream, (TOOL_HOME / name).open("wb") as output:
                    shutil.copyfileobj(stream, output)
                (TOOL_HOME / name).chmod(0o755)
    solc = TOOL_HOME / ("solc" + suffix)
    download(pin["solc_url"], solc, pin["solc_sha256"])
    solc.chmod(0o755)
    hashes = {name: hashlib.sha256((TOOL_HOME / name).read_bytes()).hexdigest() for name in names}
    hashes[solc.name] = pin["solc_sha256"]
    (TOOL_HOME / "installed.json").write_text(json.dumps(hashes, sort_keys=True), encoding="utf-8")
    tools()  # Verify released version strings as well as download checksums.
    print(
        "Installed checksum-pinned Foundry "
        + PIN["foundry_version"]
        + " / Solidity "
        + PIN["solidity_version"]
        + " in the ignored project runtime."
    )


def run(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        args,
        cwd=PROJECT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        creationflags=FLAGS,
    )
    if result.returncode:
        # Forge traces can contain environment-supplied signing keys. Never print raw output.
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode})")
    return result.stdout


def tools() -> dict[str, str]:
    suffix = ".exe" if sys.platform == "win32" else ""
    manifest = json.loads((TOOL_HOME / "installed.json").read_text(encoding="utf-8"))
    paths = {name: str(TOOL_HOME / (name + suffix)) for name in ("forge", "cast", "anvil", "solc")}
    for name, path in paths.items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != manifest[name + suffix]:
            raise RuntimeError("Installed toolchain bytes changed; reinstall from pinned archives")
        version = run([path, "--version"])
        wanted = PIN["solidity_version"] if name == "solc" else PIN["foundry_version"]
        if wanted not in version or "nightly" in version.lower() or "develop" in version.lower():
            raise RuntimeError("Unexpected toolchain version")
    return paths


def tests(paths: dict[str, str]) -> dict[str, Any]:
    run([paths["forge"], "fmt", "--check"])
    raw = run([paths["forge"], "test", "--use", paths["solc"], "--offline", "--json"])
    report = json.loads(raw)
    results = [test for suite in report.values() for test in suite["test_results"].values()]
    if not results or any(test["status"] != "Success" for test in results):
        raise RuntimeError("Solidity tests failed")
    compiled = json.loads((PROJECT / "out/AlertRegistry.sol/AlertRegistry.json").read_bytes())
    expected_abi = json.loads((PROJECT / "abi/AlertRegistry.json").read_bytes())
    if compiled["abi"] != expected_abi:
        raise RuntimeError("Committed contract ABI differs from compiled source")
    print(f"Forge: {len(results)} tests passed (256 cases per fuzz test).")
    return report


def rpc(url: str, method: str, params: list[Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(dict(jsonrpc="2.0", id=1, method=method, params=params)).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        result = json.load(response)
    if "error" in result:
        raise RuntimeError("Local RPC request rejected")
    return result["result"]


def broadcast(paths: dict[str, str], script: str, url: str, env: dict[str, str]) -> dict[str, Any]:
    run(
        [
            paths["forge"],
            "script",
            f"script/{script}.s.sol:{script}",
            "--use",
            paths["solc"],
            "--offline",
            "--rpc-url",
            url,
            "--broadcast",
            "--slow",
        ],
        env=env,
    )
    path = PROJECT / "broadcast" / (script + ".s.sol") / "31337" / "run-latest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def smoke(paths: dict[str, str]) -> dict[str, Any]:
    from uuid import UUID

    # Import only the pure wire mapping, never settings or application database initialization.
    from firewatch.evidence.anchor import alert_key, evidence_digest

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
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
                if rpc(url, "eth_chainId", []) == "0x7a69":
                    break
            except (OSError, RuntimeError):
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Isolated loopback Anvil did not start")
            time.sleep(0.1)
        wallet_result = json.loads(run([paths["cast"], "wallet", "new", "--json"]))
        if wallet_result.get("success") is not True:
            raise RuntimeError("Dedicated test signer generation failed")
        wallet = wallet_result["data"][0]
        writer = wallet["address"]
        rpc(url, "anvil_setBalance", [writer, hex(10**20)])
        env = dict(os.environ)
        env.update(
            FIREWATCH_DEPLOY_PRIVATE_KEY=wallet["private_key"],
            FIREWATCH_DEPLOY_CHAIN_ID="31337",
            FIREWATCH_REGISTRY_WRITER=writer,
        )
        deployment = broadcast(paths, "DeployAlertRegistry", url, env)
        address = deployment["transactions"][0]["contractAddress"]
        payload = (ROOT / "docs" / "evidence-v1-example.json").read_bytes()
        alert_id = UUID(json.loads(payload)["alert_id"])
        key = alert_key(alert_id)
        digest = evidence_digest(payload, hashlib.sha256(payload).hexdigest())
        env.update(
            FIREWATCH_SMOKE_REGISTRY=address,
            FIREWATCH_SMOKE_ALERT_KEY=key,
            FIREWATCH_SMOKE_DIGEST=digest,
        )
        anchored = broadcast(paths, "AnchorLocalEvidence", url, env)
        receipts = [
            rpc(url, "eth_getTransactionReceipt", [tx["hash"]]) for tx in anchored["transactions"]
        ]
        if len(receipts) != 2 or any(r["status"] != "0x1" for r in receipts):
            raise RuntimeError("Local anchor transactions did not both succeed")
        if len(receipts[0]["logs"]) != 1 or receipts[1]["logs"]:
            raise RuntimeError("Anchor/retry event behavior differs from contract")
        topics = receipts[0]["logs"][0]["topics"]
        if topics[1].lower() != key or topics[2].lower() != digest:
            raise RuntimeError("Event did not preserve exact backend bytes32 values")
        selector = run([paths["cast"], "sig", "evidenceDigest(bytes32)"]).strip()
        observed = rpc(url, "eth_call", [{"to": address, "data": selector + key[2:]}, "latest"])
        if observed.lower() != digest:
            raise RuntimeError("Stored digest differs from exact backend SHA-256")
        anchor_selector = run([paths["cast"], "sig", "anchor(bytes32,bytes32)"]).strip()
        data = anchor_selector + key[2:] + digest[2:]
        for sender, call_data in [
            ("0x" + "11" * 20, data),
            (writer, anchor_selector + key[2:] + "22" * 32),
        ]:
            try:
                rpc(url, "eth_call", [{"from": sender, "to": address, "data": call_data}, "latest"])
            except RuntimeError:
                continue
            raise RuntimeError("Unauthorized/conflicting local call was not rejected")
        code = rpc(url, "eth_getCode", [address, "latest"])
        # Check Foundry's generated outputs before asserting that a signing key was not persisted.
        key_forms = [
            wallet["private_key"].encode(),
            wallet["private_key"][2:].encode(),
            str(int(wallet["private_key"], 16)).encode(),
        ]
        for directory in ("broadcast", "cache", "out"):
            for file in (PROJECT / directory).rglob("*"):
                if file.is_file() and any(secret in file.read_bytes() for secret in key_forms):
                    file.unlink()
                    raise RuntimeError(
                        "Generated signing output contained a private key and was removed"
                    )
        return dict(
            network_kind="local",
            chain_id=31337,
            rpc_bind="127.0.0.1",
            contract_address=address,
            writer_address=writer,
            deployment_transaction=deployment["transactions"][0]["hash"],
            anchor_transaction=anchored["transactions"][0]["hash"],
            retry_transaction=anchored["transactions"][1]["hash"],
            alert_key=key,
            evidence_sha256=digest[2:],
            deployed_code_sha256=hashlib.sha256(bytes.fromhex(code[2:])).hexdigest(),
            source_kind="committed synthetic canonical evidence example",
            authorized_write=True,
            exact_digest_readback=True,
            event_verified=True,
            identical_retry_no_event=True,
            unauthorized_rejected=True,
            conflict_rejected=True,
            dedicated_random_test_signer=True,
            public_anvil_keys_used=False,
            persisted_private_key=False,
            application_database_access=False,
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "test", "smoke"])
    parser.add_argument("--receipt", type=Path, default=ROOT / "artifacts" / "phase-5.3-smoke.json")
    args = parser.parse_args()
    try:
        if args.action == "install":
            install()
        else:
            paths = tools()
            report = tests(paths)
            if args.action == "smoke":
                result = smoke(paths)
                result.update(
                    schema_version="phase-5.3-local-contract-acceptance-v1",
                    recorded_at=datetime.now(UTC).isoformat(),
                    foundry_version=PIN["foundry_version"],
                    solidity_version=PIN["solidity_version"],
                    forge_tests=sum(len(s["test_results"]) for s in report.values()),
                    local_chain_stopped=True,
                    generated_outputs_key_scan_passed=True,
                )
                sources = [
                    "contracts/src/AlertRegistry.sol",
                    "contracts/test/AlertRegistry.t.sol",
                    "contracts/script/DeployAlertRegistry.s.sol",
                    "contracts/script/AnchorLocalEvidence.s.sol",
                    "contracts/foundry.toml",
                    "contracts/toolchain.json",
                    "contracts/abi/AlertRegistry.json",
                    "src/firewatch/evidence/anchor.py",
                    "scripts/contract_tools.py",
                ]
                result["files_sha256"] = {
                    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources
                }
                args.receipt.parent.mkdir(parents=True, exist_ok=True)
                args.receipt.write_text(
                    json.dumps(result, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                print("Isolated Anvil deployment, SHA-256 anchoring and retry checks passed.")
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        tarfile.TarError,
    ):
        print(
            "Contract tooling failed; verify pinned tools, compilation and local RPC availability. "
            "Raw signing-process output is deliberately suppressed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
