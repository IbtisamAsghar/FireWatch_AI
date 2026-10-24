"""Long-path integrity, checkout-stable hashes and safe database restoration."""

import hashlib
import json
import os
import shutil
import subprocess
from unittest.mock import MagicMock

import pytest

from firewatch.ml.registry_artifacts import _read
from firewatch.ml.split_package import _safe_file
from firewatch.paths import filesystem_path
from scripts.delivery_inventory import file_hash, generate, verify
from scripts.prepare_local import cache_asset, relocated_asset, restore


def test_long_package_path_is_readable_and_stays_inside_root(tmp_path):
    relative = "/".join(["nested-evidence-" + "x" * 45] * 5) + "/followup.json"
    target = filesystem_path(tmp_path / relative)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"evidence":true}')
    assert _read(_safe_file(tmp_path, relative), 100) == b'{"evidence":true}'
    with pytest.raises(ValueError, match="Unsafe"):
        _safe_file(tmp_path, "../escape.json")
    with pytest.raises(ValueError, match="byte bound"):
        _read(_safe_file(tmp_path, relative), 2)
    # Python's ordinary Windows rmtree cannot clean the deep fixture either.
    # Remove our owned fixture using the same I/O support before pytest cleanup.
    shutil.rmtree(filesystem_path(tmp_path))


def test_inventory_survives_text_checkout_but_detects_exact_artifact_change(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init")
    (tmp_path / ".gitattributes").write_text("*.py text eol=lf\ndata/** -text\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "source.py").write_bytes(b"value = 1\n")
    (tmp_path / "data/evidence.json").write_bytes(b'{"original":true}\r\n')
    git("add", ".")
    manifest = generate(tmp_path)
    (tmp_path / "docs/branch-delivery-inventory.json").write_text(json.dumps(manifest))
    (tmp_path / "source.py").write_bytes(b"value = 1\r\n")
    assert verify(tmp_path) == []
    (tmp_path / "data/evidence.json").write_bytes(b'{"original":true}\n')
    assert verify(tmp_path) == ["Delivery checksum mismatch: data/evidence.json"]
    assert (
        file_hash(tmp_path / "source.py", exact=False) == hashlib.sha256(b"value = 1\n").hexdigest()
    )


def test_asset_relocation_requires_containment_and_pinned_bytes(tmp_path):
    target = tmp_path / "data/raw/source.csv"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"real source bytes")
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    assert (
        relocated_asset(tmp_path, "C:/Users/original/FireWatch_AI/data/raw/source.csv", checksum)
        == target
    )
    for name, sha in [("../outside.csv", checksum), (str(target), "a" * 64)]:
        with pytest.raises(ValueError):
            relocated_asset(tmp_path, name, sha)


def test_restore_refuses_nonempty_database_before_launching_pg_restore(monkeypatch, tmp_path):
    from firewatch.config import Settings

    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = (1,)
    opened = MagicMock()
    opened.__enter__.return_value = connection
    monkeypatch.setattr("scripts.prepare_local.psycopg.connect", lambda **kwargs: opened)
    monkeypatch.setattr(
        "scripts.prepare_local.subprocess.run", lambda *a, **k: pytest.fail("Restore launched")
    )
    settings = Settings(
        database_url="postgresql+psycopg://test:test-password@127.0.0.1:55432/firewatch",
        admin_api_token="test-token-" + "x" * 40,
        _env_file=None,
    )
    with pytest.raises(ValueError, match="empty database"):
        restore(settings, tmp_path, tmp_path / "backup.dump")


def test_historical_source_cache_is_content_addressed_and_rejects_tampering(tmp_path):
    original = tmp_path / "data/archive-imports/original.csv"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"original satellite observations")
    checksum = hashlib.sha256(original.read_bytes()).hexdigest()
    raw = tmp_path / "data/raw"
    installed = cache_asset(tmp_path, original, checksum, raw)
    assert installed.is_relative_to(raw)
    assert installed.read_bytes() == original.read_bytes()
    assert cache_asset(tmp_path, original, checksum, raw) == installed
    installed.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="different bytes"):
        cache_asset(tmp_path, original, checksum, raw)
    assert original.read_bytes() == b"original satellite observations"


@pytest.mark.skipif(os.name != "nt", reason="Windows path prefix")
def test_windows_extended_path_is_idempotent(tmp_path):
    path = filesystem_path(tmp_path / "artifact.json")
    assert str(path).startswith("\\\\?\\")
    assert filesystem_path(path) == path


def test_native_setup_refuses_to_reuse_a_runtime_on_another_port(tmp_path, monkeypatch):
    from scripts import native_postgres

    (tmp_path / "bootstrap.json").write_text('{"port":5432}')
    monkeypatch.setattr(native_postgres, "protect", lambda *a, **k: None)
    monkeypatch.setattr(
        native_postgres, "start", lambda *a: pytest.fail("Existing server was started")
    )
    with pytest.raises(RuntimeError, match="separate --home"):
        native_postgres.setup(tmp_path, 55432)
