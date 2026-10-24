"""Reproducible SHA-256 delivery inventory with explicit checkout text semantics."""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = "docs/branch-delivery-inventory.json"
SEMANTICS = "lf-normalized-text-exact-binary-v1"


def filesystem_path(path: Path) -> Path:
    """Keep this dependency-free script usable before installing the package."""
    if os.name != "nt":
        return path
    absolute = os.path.abspath(path)
    if absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def file_hash(path: Path, *, exact: bool = True) -> str:
    raw = filesystem_path(path).read_bytes()
    # Explicit -text paths remain byte-exact. Auto text follows Git's binary
    # detection window; source text hashes are stable under CRLF/LF checkout.
    if not exact and b"\0" not in raw[:8000]:
        raw = raw.replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def exact_paths(root: Path, names: list[str]) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "check-attr", "-z", "--stdin", "text"],
        input="\0".join(names) + "\0",
        text=True,
        capture_output=True,
        check=True,
    )
    parts = result.stdout.split("\0")
    return {parts[i] for i in range(0, len(parts) - 2, 3) if parts[i + 2] == "unset"}


def generate(root: Path = ROOT, include: tuple[str, ...] = ()) -> dict:
    # Tracked files plus explicit reviewed additions only: ignored secrets,
    # runtimes, caches and scratch files cannot enter a delivery automatically.
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    for name in include:
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or not name.startswith(("src/", "scripts/", "tests/", "docs/", "output/"))
        ):
            raise ValueError(
                "Additional inventory files must be reviewed source/test/document/report paths"
            )
    names = sorted((set(result.stdout.split("\0")) | set(include)) - {"", INVENTORY})
    exact = exact_paths(root, names)
    return {
        "schema_version": "branch-delivery-inventory-v2",
        "branch": "devtalha",
        "hash_semantics": SEMANTICS,
        "inventory_excludes_itself": True,
        "files": len(names),
        "total_bytes": sum(filesystem_path(root / name).stat().st_size for name in names),
        "exact_byte_paths": sorted(exact),
        "excluded_categories": [
            "Untracked files not explicitly reviewed, including secrets, runtimes and caches",
            "The delivery inventory itself (avoids a circular checksum)",
        ],
        "files_sha256": {name: file_hash(root / name, exact=name in exact) for name in names},
    }


def verify(root: Path = ROOT) -> list[str]:
    manifest = json.loads((root / INVENTORY).read_text(encoding="utf-8"))
    normalized = manifest.get("hash_semantics") == SEMANTICS
    exact = set(manifest.get("exact_byte_paths", []))
    errors = []
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    )
    for name in sorted(
        set(tracked.stdout.split("\0")) - {"", INVENTORY} - set(manifest["files_sha256"])
    ):
        errors.append(f"Tracked file absent from delivery inventory: {name}")
    for name, expected in manifest["files_sha256"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts or "\\" in name or ":" in name:
            errors.append("Unsafe delivery inventory path")
            continue
        path = filesystem_path(root / name)
        if not path.is_file() or path.is_symlink():
            errors.append(f"Missing or nonregular delivery file: {name}")
        elif file_hash(path, exact=not normalized or name in exact) != expected:
            errors.append(f"Delivery checksum mismatch: {name}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write", action="store_true", help="Regenerate from reviewed tracked files"
    )
    parser.add_argument(
        "--include", action="append", default=[], help="Include a new reviewed source file"
    )
    args = parser.parse_args()
    if args.write:
        manifest = generate(include=tuple(args.include))
        (ROOT / INVENTORY).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"Inventoried {manifest['files']} tracked files; exact artifacts, normalized text")
        return 0
    errors = verify()
    for error in errors:
        print(error)
    print(f"Delivery inventory: {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
