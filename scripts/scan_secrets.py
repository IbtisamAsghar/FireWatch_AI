"""Checksum-pinned Gitleaks: redacted history and nonignored worktree scanning."""

import hashlib
import io
import json
import os
import platform
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "8.30.1"
ASSETS = {
    "Linux": (
        "linux_x64.tar.gz",
        "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
    ),
    "Windows": (
        "windows_x64.zip",
        "d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e",
    ),
}


def executable() -> Path:
    if platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Scanner supports x86_64 Linux/Windows")
    suffix, expected = ASSETS[platform.system()]
    target = ROOT / ".local/security"
    target.mkdir(parents=True, exist_ok=True)
    archive = target / f"gitleaks_{VERSION}_{suffix}"
    if not archive.exists():
        url = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}/{archive.name}"
        with urllib.request.urlopen(url, timeout=60) as response:
            content = response.read(32 * 1024 * 1024 + 1)
        if len(content) > 32 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected:
            raise RuntimeError("Scanner archive failed SHA-256 verification")
        archive.write_bytes(content)
    content = archive.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise RuntimeError("Cached scanner archive failed SHA-256 verification")
    name = "gitleaks.exe" if platform.system() == "Windows" else "gitleaks"
    if suffix.endswith("zip"):
        with zipfile.ZipFile(io.BytesIO(content)) as package:
            binary = package.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as package:
            member = package.getmember(name)
            if not member.isfile():
                raise RuntimeError("Scanner archive has no regular executable")
            stream = package.extractfile(member)
            assert stream is not None
            binary = stream.read()
    path = target / name
    path.write_bytes(binary)
    path.chmod(0o700)
    return path


def main() -> int:
    binary = executable()
    output = ROOT / "artifacts/security"
    output.mkdir(parents=True, exist_ok=True)
    # Never let ignored local .env, raw archives, wallets or build outputs enter a report.
    paths = (
        subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    with tempfile.TemporaryDirectory(prefix="fw-secrets-") as folder:
        snapshot = Path(folder)
        # Negative control: fail if scanner installation/configuration silently misses keys.
        probe = snapshot / "probe"
        probe.mkdir()
        (probe / "config.txt").write_text(
            'api_key = "' + secrets.token_hex(24) + '"\n', encoding="utf-8"
        )
        probe_report = snapshot / "probe.json"
        probe_result = subprocess.run(
            [
                str(binary),
                "dir",
                str(probe),
                "--no-banner",
                "--redact=100",
                f"--config={ROOT / '.gitleaks.toml'}",
                "--ignore-gitleaks-allow",
                "--report-format=json",
                f"--report-path={probe_report}",
            ],
            cwd=ROOT,
            capture_output=True,
        )
        if probe_result.returncode != 1 or not json.loads(probe_report.read_text()):
            raise RuntimeError("Secret scanner negative control failed")
        # Scan only the repository snapshot, separate from this deliberately leaking probe.
        snapshot = snapshot / "repository"
        snapshot.mkdir()
        for name in sorted(set(paths) - {""}):
            relative = Path(name)
            source = ROOT / relative
            if not source.exists():
                continue  # Deleted tracked file; history still scans it.
            if relative.is_absolute() or ".." in relative.parts or source.is_symlink():
                raise RuntimeError("Unsafe repository scan path")
            if not source.is_file() or not source.resolve().is_relative_to(ROOT.resolve()):
                raise RuntimeError("Unsafe repository scan file")
            destination = snapshot / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        failed = False
        for mode, location, extra in (
            ("git", ROOT, ["--log-opts=--all"]),
            ("dir", snapshot, []),
        ):
            report = output / f"gitleaks-{mode}.json"
            args = [
                str(binary),
                mode,
                str(location),
                "--no-banner",
                "--redact=100",
                f"--config={ROOT / '.gitleaks.toml'}",
                "--ignore-gitleaks-allow",
                f"--gitleaks-ignore-path={snapshot / 'no-ignored-findings'}",
                "--report-format=json",
                f"--report-path={report}",
                *extra,
            ]
            environment = dict(os.environ)
            # Ignore machine/user scanner configuration; use the reviewed repository config only.
            environment.pop("GITLEAKS_CONFIG", None)
            environment.pop("GITLEAKS_CONFIG_TOML", None)
            result = subprocess.run(args, cwd=ROOT, capture_output=True, env=environment)
            if result.returncode not in {0, 1} or not report.is_file():
                raise RuntimeError("Secret scanner failed; no passing result")
            findings = json.loads(report.read_text(encoding="utf-8")) or []
            # Raw scanner stderr/stdout is deliberately withheld.
            print(f"Gitleaks {mode}: {len(findings)} findings (redacted report saved)")
            failed |= result.returncode != 0 or bool(findings)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
