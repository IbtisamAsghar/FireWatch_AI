"""Deployment builds must reject modified, extra, or escaping artifacts."""

import json

import pytest

from scripts.render_assets import digest, verify


@pytest.fixture
def package(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "model").mkdir()
    content = b"public historical observation\r\n"
    name = f"assets/{digest(content)}.bin"
    (tmp_path / name).write_bytes(content)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {"schema_version": "render-artifacts-v1", "files_sha256": {name: digest(content)}}
        )
    )
    return tmp_path, name


def test_package_rejects_tampered_and_unpinned_bytes(package):
    root, name = package
    verify(root)
    (root / "assets/extra.bin").write_bytes(b"unreviewed")
    with pytest.raises(ValueError, match="unpinned"):
        verify(root)
    (root / "assets/extra.bin").unlink()
    (root / name).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        verify(root)


@pytest.mark.parametrize("name", ["../outside", "/outside", r"assets\outside", "other/file"])
def test_package_rejects_paths_outside_reviewed_directories(package, name):
    root, _ = package
    (root / "manifest.json").write_text(
        json.dumps({"schema_version": "render-artifacts-v1", "files_sha256": {name: "0" * 64}})
    )
    with pytest.raises(ValueError, match="Unsafe"):
        verify(root)
