"""Toolchain checksums and process-output secrecy; no downloads or live subprocesses."""

import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from scripts import contract_tools as tooling


def test_download_checks_trusted_digest_before_replacing_destination(tmp_path, monkeypatch):
    destination = tmp_path / "tool.exe"
    destination.write_bytes(b"original")
    monkeypatch.setattr(tooling.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"changed"))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        tooling.download("https://example.invalid/tool", destination, "f" * 64)
    assert destination.read_bytes() == b"original"
    assert not destination.with_suffix(".exe.partial").exists()


def test_verified_download_is_cached_without_network(tmp_path, monkeypatch):
    destination = tmp_path / "tool.exe"
    payload = b"synthetic pinned tool"
    destination.write_bytes(payload)
    monkeypatch.setattr(tooling.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network"))
    tooling.download(
        "https://example.invalid/tool", destination, hashlib.sha256(payload).hexdigest()
    )
    assert destination.read_bytes() == payload


def test_successful_download_publishes_only_verified_bytes(tmp_path, monkeypatch):
    destination = tmp_path / "tool.exe"
    payload = b"synthetic pinned tool"
    monkeypatch.setattr(tooling.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(payload))
    tooling.download(
        "https://example.invalid/tool", destination, hashlib.sha256(payload).hexdigest()
    )
    assert destination.read_bytes() == payload


def test_subprocess_failure_never_prints_signing_output(monkeypatch):
    monkeypatch.setattr(
        tooling.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(
            returncode=1, stdout="PRIVATE_SIGNER_SECRET", stderr="PRIVATE_SIGNER_SECRET"
        ),
    )
    with pytest.raises(RuntimeError) as error:
        tooling.run(["forge", "script"])
    assert "PRIVATE_SIGNER_SECRET" not in str(error.value)


def test_changed_installed_binary_is_rejected_before_execution(tmp_path, monkeypatch):
    suffix = ".exe" if tooling.sys.platform == "win32" else ""
    name = "forge" + suffix
    (tmp_path / name).write_bytes(b"changed")
    (tmp_path / "installed.json").write_text(json.dumps({name: "f" * 64}))
    monkeypatch.setattr(tooling, "TOOL_HOME", tmp_path)
    monkeypatch.setattr(tooling, "run", lambda *a, **k: pytest.fail("executed modified tool"))
    with pytest.raises(RuntimeError, match="bytes changed"):
        tooling.tools()


def test_committed_abi_exposes_only_the_minimal_integrity_surface():
    abi = json.loads((tooling.PROJECT / "abi/AlertRegistry.json").read_bytes())
    functions = {entry["name"] for entry in abi if entry["type"] == "function"}
    assert functions == {"writer", "anchor", "evidenceDigest"}
    anchor = next(e for e in abi if e.get("name") == "anchor")
    assert [item["type"] for item in anchor["inputs"]] == ["bytes32", "bytes32"]
    event = next(e for e in abi if e["type"] == "event")
    assert event["name"] == "EvidenceAnchored"
    assert [item["type"] for item in event["inputs"]] == ["bytes32", "bytes32", "address"]
    assert all(item["indexed"] for item in event["inputs"])
    pin = json.loads((tooling.PROJECT / "toolchain.json").read_bytes())
    assert pin["foundry_version"] == "1.8.6" and pin["solidity_version"] == "0.8.30"
