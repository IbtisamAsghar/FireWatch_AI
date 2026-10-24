"""Explicit container storage initialization and bounded managed-model transfer."""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from firewatch.ml.registry_artifacts import managed_path, verify_managed_release

STORAGE = Path("/var/lib/firewatch")


def init_storage(root: Path = STORAGE) -> None:
    if os.name != "posix" or getattr(os, "geteuid", lambda: -1)() != 0:
        raise ValueError("Storage initialization requires the dedicated Linux init container")
    for name in ("raw", "models", "replays", "chain"):
        path = root / name
        if path.is_symlink() or not path.is_dir():
            raise ValueError("Expected a mounted storage directory")
        # Only volume roots: never recursively rewrite existing artifact permissions/bytes.
        chown = getattr(os, "chown", None)
        if chown is None:
            raise ValueError("Linux ownership support is required")
        chown(path, 10001, 10001)
        path.chmod(0o750)


def _regular(base: Path, name: str) -> Path:
    path = managed_path(base, name)
    if not path.is_file() or path.is_symlink():
        raise ValueError("Expected a regular artifact file")
    return path


def install_model(source: Path, expected_sha256: str, destination: Path) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("Supply a trusted release-manifest SHA-256")
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("Model transfer roots must not be symlinks")
    source = source.resolve(strict=True)
    manifest = verify_managed_release(source, expected_sha256)
    version, bundle_sha = manifest["version"], manifest["bundle_sha256"]
    if not isinstance(version, str) or not re.fullmatch(r"[a-z][a-z0-9._-]{0,99}", version):
        raise ValueError("Invalid versioned release")
    if not isinstance(bundle_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", bundle_sha):
        raise ValueError("Invalid bundle identity")
    target = managed_path(destination, f"releases/{version}/{bundle_sha}")
    if target.exists():
        verify_managed_release(target, expected_sha256)
        return {"version": version, "release_sha256": expected_sha256, "already_installed": True}
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".transfer-", dir=target.parent))
    try:
        total = 0
        names = ["release-manifest.json", *manifest["files"]]
        if len(names) > 4096:
            raise ValueError("Model inventory exceeds transfer limit")
        for name in names:
            original = _regular(source, name)
            target_file = managed_path(staging, name)
            with original.open("rb") as stream:
                content = stream.read(64 * 1024 * 1024 + 1)
            total += len(content)
            if len(content) > 64 * 1024 * 1024 or total > 321 * 1024 * 1024:
                raise ValueError("Model transfer exceeds byte limits")
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_bytes(content)
            target_file.chmod(0o640)
        verify_managed_release(staging, expected_sha256)
        staging.rename(target)
    finally:
        if staging.exists():
            if staging.parent.resolve() != target.parent.resolve() or not staging.name.startswith(
                ".transfer-"
            ):
                raise ValueError("Unsafe transfer cleanup path")
            shutil.rmtree(staging)
    return {"version": version, "release_sha256": expected_sha256, "already_installed": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-storage")
    model = commands.add_parser("install-model")
    model.add_argument("--source", type=Path, required=True)
    model.add_argument("--release-sha256", required=True)
    model.add_argument("--destination", type=Path, default=STORAGE / "models")
    args = parser.parse_args(argv)
    try:
        if args.command == "init-storage":
            init_storage()
            print("Storage roots prepared for UID/GID 10001")
        else:
            print(json.dumps(install_model(args.source, args.release_sha256, args.destination)))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print(
            "Artifact operation refused; verify mounts, release identity and inventory.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
