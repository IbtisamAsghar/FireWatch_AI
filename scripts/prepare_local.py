"""Provision a private native database; optionally restore into an empty target."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import psycopg
from sqlalchemy.engine import make_url

from firewatch.config import Settings
from firewatch.paths import filesystem_path

try:
    from scripts import native_postgres
except ModuleNotFoundError:
    import native_postgres

ROOT = Path(__file__).resolve().parents[1]


def relocated_asset(root: Path, stored: str, expected_hash: str) -> Path:
    """Rebase a storage locator only after proving its exact pinned bytes."""
    parts = PureWindowsPath(stored).parts
    markers = [i for i, part in enumerate(parts) if part.casefold() == "firewatch_ai"]
    if markers:
        relative = Path(*parts[markers[-1] + 1 :])
    else:
        relative = Path(stored)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Source storage locator cannot be safely relocated")
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Relocated source escapes the workspace")
    path = filesystem_path(target)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Relocated source file is unavailable")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
            raise ValueError("Relocated source checksum mismatch")
    return target


def restore(settings: Settings, home: Path, dump: Path) -> None:
    """Restore atomically, refuse every nonempty database; never use --clean."""
    url = make_url(settings.database_url.get_secret_value())
    environment = dict(
        os.environ,
        PGHOST=str(url.host),
        PGPORT=str(url.port),
        PGUSER=str(url.username),
        PGPASSWORD=str(url.password),
        PGDATABASE=str(url.database),
    )
    connection_args = dict(
        host=url.host, port=url.port, user=url.username, password=url.password, dbname=url.database
    )
    with psycopg.connect(**connection_args) as connection:
        occupied = connection.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f')"
        ).fetchone()[0]
        if occupied:
            raise ValueError("Restore requires an empty database; existing data was preserved")
    result = subprocess.run(
        [
            native_postgres.executable(home, "pg_restore"),
            "--exit-on-error",
            "--single-transaction",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            str(url.database),
            str(dump.resolve()),
        ],
        env=environment,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        raise RuntimeError("Historical restore failed; transaction rolled back")
    rebase_sources(settings)


def cache_asset(root: Path, target: Path, checksum: str, raw_root: Path) -> Path:
    """Place out-of-cache historical sources under the existing audit allowlist."""
    roots = [raw_root.resolve(), (root / "data/replays-ready-v1").resolve()]
    if any(target.resolve().is_relative_to(base) for base in roots):
        return target
    destination = raw_root / "restored-assets" / (checksum + target.suffix)
    path = filesystem_path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("Existing restored cache entry has different bytes")
    else:
        shutil.copyfile(filesystem_path(target), path)
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("Source changed during restored cache installation")
    return destination


def rebase_sources(settings: Settings) -> None:
    # Storage locators are mutable metadata. Never rewrite canonical alert
    # payloads, completed-run manifests, scores or the original model approval.
    url = make_url(settings.database_url.get_secret_value())
    connection_args = dict(
        host=url.host, port=url.port, user=url.username, password=url.password, dbname=url.database
    )
    with psycopg.connect(**connection_args) as connection:
        rows = connection.execute(
            "SELECT id, storage_path, content_hash FROM source_assets"
        ).fetchall()
        relocated = 0
        unavailable = 0
        for identifier, stored, checksum in rows:
            try:
                target = relocated_asset(ROOT, stored, checksum)
                target = cache_asset(ROOT, target, checksum, ROOT / settings.raw_data_dir)
            except ValueError:
                unavailable += 1
                continue
            connection.execute(
                "UPDATE source_assets SET storage_path=%s WHERE id=%s", (str(target), identifier)
            )
            relocated += 1
    print(
        f"Historical sources: {relocated} verified locators installed/rebased, "
        f"{unavailable} unavailable."
    )
    print("Historical payloads, scores, model approval and final-test ledger were not rewritten.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=55432)
    parser.add_argument("--home", type=Path)
    parser.add_argument(
        "--restore", action="store_true", help="Restore delivered dump into empty DB"
    )
    parser.add_argument(
        "--relocate-sources",
        action="store_true",
        help="Repair source locators in an existing restore",
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Use the Compose workflow on non-Windows hosts")
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")
    home = args.home or Path(os.environ["LOCALAPPDATA"]) / "FireWatch/PostgreSQL16-devtalha"
    try:
        state = native_postgres.setup(home.resolve(), args.port)
        settings = Settings(_env_file=ROOT / ".env")
        url = make_url(settings.database_url.get_secret_value())
        if (
            url.host != "127.0.0.1"
            or url.port != state["port"]
            or url.database != "firewatch"
            or url.username != "firewatch"
            or url.password != state["app_password"]
        ):
            raise ValueError("Existing .env selects another database; it was preserved")
        if args.restore and args.relocate_sources:
            raise ValueError("Choose restore or source relocation")
        if args.relocate_sources:
            rebase_sources(settings)
        elif args.restore:
            restore(settings, home, ROOT / "data/database/firewatch.dump")
            content = (ROOT / ".env").read_text(encoding="utf-8")
            overrides = {
                "ACTIVE_SCORER": "registry",
                "REPLAYS_CONFIG": ".local/readiness/replays.yaml",
                "REPLAY_BUNDLE_DIR": "data/replays-ready-v1",
                "CORS_ORIGINS": '["http://localhost:3000","http://localhost:5173"]',
            }
            lines = [
                line for line in content.splitlines() if line.partition("=")[0] not in overrides
            ]
            lines += [f"{key}={value}" for key, value in overrides.items()]
            (ROOT / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
            native_postgres.protect(ROOT / ".env")
        else:
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT, capture_output=True
            )
            if result.returncode:
                raise RuntimeError("Migration failed; inspect local database configuration")
        print("Local database prepared. Run firewatch db-check and /health/ready before serving.")
        return 0
    except (OSError, RuntimeError, ValueError, psycopg.Error, subprocess.SubprocessError) as exc:
        print(
            f"Local preparation failed ({type(exc).__name__}); no credentials displayed.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
