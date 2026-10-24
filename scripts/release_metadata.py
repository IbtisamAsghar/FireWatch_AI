"""Export public build identities only; never serialize environment/configuration."""

import hashlib
import json
import platform
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, check=True
        ).stdout
    )
    files = [ROOT / name for name in ("requirements.lock", "requirements-dev.lock", "Dockerfile")]
    files += sorted((ROOT / "dist").glob("*.whl"))
    files += sorted((ROOT / "dist").glob("*.tar.gz"))
    if not any(path.suffix == ".whl" for path in files):
        raise SystemExit("Build distributions before exporting release metadata")
    result = {
        "schema_version": "build-v1",
        "revision": revision,
        "working_tree_dirty": dirty,
        "python": platform.python_version(),
        "migration_revision": "0003",
        "files": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        },
        "source_files_sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(
                path.read_bytes().replace(b"\r\n", b"\n")
            ).hexdigest()
            for path in sorted((ROOT / "src/firewatch").rglob("*.py"))
        },
    }
    output = ROOT / "artifacts/build-metadata.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Exported public build metadata")


if __name__ == "__main__":
    main()
