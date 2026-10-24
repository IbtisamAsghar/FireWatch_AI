"""Check exact pins, hashes and runtime/dev shared-version consistency."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_lock(name: str) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    text = (ROOT / name).read_text(encoding="utf-8")
    for block in re.split(r"\n(?=[A-Za-z0-9])", text):
        line = block.splitlines()[0].strip() if block.splitlines() else ""
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s;\\]+)", line)
        if not match or "--hash=sha256:" not in block:
            raise ValueError(f"{name}: each requirement must have an exact pin and SHA-256 hash")
        package = re.sub(r"[-_.]+", "-", match.group(1)).lower()
        versions.setdefault(package, set()).add(match.group(2))
    if not versions:
        raise ValueError(f"{name} contains no locked dependencies")
    return versions


def main() -> None:
    runtime = read_lock("requirements.lock")
    development = read_lock("requirements-dev.lock")
    security = read_lock("requirements-security.lock")
    for package, versions in runtime.items():
        if development.get(package) != versions:
            raise ValueError(f"Runtime/development lock mismatch: {package}")
    print(
        f"Locks agree: {len(runtime)} runtime, {len(development)} development packages; "
        f"{len(security)} isolated security-tool packages."
    )


if __name__ == "__main__":
    main()
