"""Versioned deployment transfer verifies bytes without granting model approval."""

import pytest
from inference_factory import candidate
from registry_factory import make_candidate

from firewatch import deployment
from firewatch.deployment import install_model
from firewatch.ml.registry_artifacts import verify_managed_release


@pytest.fixture(scope="module")
def fitted_transfer():
    return candidate()


def test_managed_release_transfer_and_repeat_are_verified(tmp_path, fitted_transfer):
    source = make_candidate(tmp_path / "source", fitted_transfer)
    destination = tmp_path / "installed"
    result = install_model(source.release_root, source.release_sha256, destination)
    assert not result["already_installed"]
    target = destination / "releases" / source.version / source.bundle_sha256
    verify_managed_release(target, source.release_sha256)
    assert install_model(source.release_root, source.release_sha256, destination)[
        "already_installed"
    ]
    assert not list(destination.rglob(".transfer-*"))


def test_changed_release_never_overwrites_installed_identity(tmp_path, fitted_transfer):
    source = make_candidate(tmp_path / "source", fitted_transfer)
    destination = tmp_path / "installed"
    install_model(source.release_root, source.release_sha256, destination)
    target = destination / "releases" / source.version / source.bundle_sha256
    original = (target / "bundle/model.json").read_bytes()
    (source.release_root / "bundle/model.json").write_text("corrupt")
    with pytest.raises(ValueError):
        install_model(source.release_root, source.release_sha256, destination)
    assert (target / "bundle/model.json").read_bytes() == original


def test_mid_copy_change_leaves_no_installed_release_or_staging(
    tmp_path, fitted_transfer, monkeypatch
):
    source = make_candidate(tmp_path / "source", fitted_transfer)
    destination = tmp_path / "installed"
    verify = deployment.verify_managed_release

    def changing(path, checksum):
        result = verify(path, checksum)
        if path == source.release_root:
            (path / "bundle/model.json").write_text("changed after initial verification")
        return result

    monkeypatch.setattr(deployment, "verify_managed_release", changing)
    with pytest.raises(ValueError):
        install_model(source.release_root, source.release_sha256, destination)
    assert not (destination / "releases" / source.version / source.bundle_sha256).exists()
    assert not list(destination.rglob(".transfer-*"))
