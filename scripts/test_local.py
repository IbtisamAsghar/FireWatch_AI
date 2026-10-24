"""Run offline regression in the private disposable database, never application data."""

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url

from firewatch.config import Settings
from firewatch.paths import filesystem_path

ROOT = Path(__file__).resolve().parents[1]


def remove_test_directory(directory: Path) -> None:
    """Remove only our owned temp tree, including Windows readonly Git fixtures."""
    if directory.parent.resolve() != Path(
        tempfile.gettempdir()
    ).resolve() or not directory.name.startswith("firewatch-regression-"):
        raise RuntimeError("Unsafe regression temporary directory")

    def writable_retry(function, name, error):
        path = Path(name)
        if not isinstance(error, PermissionError) or path.is_symlink() or path.is_junction():
            raise error
        path.chmod(stat.S_IREAD | stat.S_IWRITE)
        function(name)

    shutil.rmtree(filesystem_path(directory), onexc=writable_retry)


def main() -> int:
    settings = Settings(_env_file=ROOT / ".env")
    url = make_url(settings.database_url.get_secret_value())
    if url.host not in {"127.0.0.1", "localhost", "::1"} or url.database != "firewatch":
        raise SystemExit("Local regression requires the private loopback firewatch installation")
    test_url = url.set(database="firewatch_test").render_as_string(hide_password=False)
    environment = dict(
        os.environ, FIREWATCH_TEST_DATABASE_URL=test_url, FIREWATCH_OFFLINE_TESTS="1"
    )
    (ROOT / "output").mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="firewatch-regression-"))
    try:
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--basetemp=" + str(directory),
                "--junitxml=output/local-regression.xml",
                *sys.argv[1:],
            ],
            cwd=ROOT,
            env=environment,
        )
    finally:
        remove_test_directory(directory)


if __name__ == "__main__":
    raise SystemExit(main())
