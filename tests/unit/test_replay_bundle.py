"""Replay file boundary tests; every fixture here is synthetic."""

import hashlib
import json

import pytest

from firewatch.pipeline.replay_bundle import _file, _json, load_bundle


@pytest.mark.parametrize(
    "path", ["../outside", "/absolute", "C:/secret", "raw\\outside", "raw/../x"]
)
def test_bundle_paths_cannot_escape(tmp_path, path):
    with pytest.raises(ValueError):
        _file(tmp_path, path)


def test_duplicate_and_nonfinite_json_are_rejected():
    for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}'):
        with pytest.raises(ValueError):
            _json(raw)


def test_wrong_manifest_hash_fails_closed(tmp_path):
    raw = json.dumps({"schema_version": "unknown"}).encode()
    (tmp_path / "manifest.json").write_bytes(raw)
    with pytest.raises(ValueError, match="checksum"):
        load_bundle(tmp_path, "0" * 64)
    with pytest.raises(ValueError):
        load_bundle(tmp_path, hashlib.sha256(raw).hexdigest())
