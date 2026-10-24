"""Fail delivery when built Python packages differ from the reviewed source tree."""

import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def differences(root: Path, archive: Path) -> list[str]:
    expected = {
        path.relative_to(root / "src").as_posix(): path.read_bytes().replace(b"\r\n", b"\n")
        for path in (root / "src/firewatch").rglob("*.py")
    }
    if archive.suffix == ".whl":
        with zipfile.ZipFile(archive) as wheel:
            actual = {
                name: wheel.read(name).replace(b"\r\n", b"\n")
                for name in wheel.namelist()
                if name.startswith("firewatch/") and name.endswith(".py")
            }
    else:
        actual = {}
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                relative = member.name.partition("/")[2]
                if relative.startswith("src/firewatch/") and relative.endswith(".py"):
                    stream = source.extractfile(member)
                    if stream is not None:
                        actual[relative.removeprefix("src/")] = stream.read().replace(
                            b"\r\n", b"\n"
                        )
    return sorted(
        name for name in expected.keys() | actual.keys() if expected.get(name) != actual.get(name)
    )


def main() -> int:
    archives = [*sorted((ROOT / "dist").glob("*.whl")), *sorted((ROOT / "dist").glob("*.tar.gz"))]
    if not any(p.suffix == ".whl" for p in archives) or not any(
        p.name.endswith(".tar.gz") for p in archives
    ):
        raise SystemExit("Build both wheel and source distributions before checking delivery")
    failed = False
    for archive in archives:
        changed = differences(ROOT, archive)
        print(f"{archive.name}: {len(changed)} source differences")
        for name in changed:
            print(f"  {name}")
        failed |= bool(changed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
