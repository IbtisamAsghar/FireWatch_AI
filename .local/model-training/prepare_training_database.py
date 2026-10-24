"""Compatibility entrypoint for portable local database provisioning."""

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").is_file())
    return subprocess.call(
        [sys.executable, str(root / "scripts/prepare_local.py"), *sys.argv[1:]], cwd=root
    )


if __name__ == "__main__":
    raise SystemExit(main())
