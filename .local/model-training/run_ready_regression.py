"""Compatibility wrapper for the portable disposable-database regression runner."""

import subprocess
import sys
from pathlib import Path

root = next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").is_file())
raise SystemExit(subprocess.call([sys.executable, str(root / "scripts/test_local.py"), *sys.argv[1:]], cwd=root))
