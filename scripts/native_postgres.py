"""Manage a private native PostgreSQL 16 installation on Windows (no Docker).

Data and bootstrap credentials live under Local AppData, outside the checkout
and OneDrive. Uses official EDB binary archives over HTTPS. The recorded SHA-256
is a local integrity record, not an independently authenticated vendor checksum.
"""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
VERSION = "16.15-1"
DOWNLOAD = f"https://get.enterprisedb.com/postgresql/postgresql-{VERSION}-windows-x64-binaries.zip"


def run(command: list[str], *, timeout: int = 120) -> None:
    result = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        # Do not echo connection details, SQL, environment, or credentials.
        raise RuntimeError(f"{Path(command[0]).name} failed (exit {result.returncode})")


def protect(path: Path, directory: bool = False) -> None:
    identity = subprocess.check_output(["whoami"], text=True).strip()
    rights = "(OI)(CI)F" if directory else "F"
    run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{identity}:{rights}"])


def executable(home: Path, name: str) -> str:
    return str(home / "pgsql" / "bin" / f"{name}.exe")


def start(home: Path, port: int) -> None:
    status = subprocess.run(
        [executable(home, "pg_ctl"), "status", "-D", str(home / "data")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if status.returncode:
        run(
            [
                executable(home, "pg_ctl"),
                "start",
                "-D",
                str(home / "data"),
                "-l",
                str(home / "server.log"),
                "-w",
                "-t",
                "45",
            ],
            timeout=55,
        )
    run([executable(home, "pg_isready"), "-h", "127.0.0.1", "-p", str(port)])
    print(f"PostgreSQL 16 ready on 127.0.0.1:{port}", flush=True)


def setup(home: Path, requested_port: int) -> dict:
    import psycopg
    from psycopg import sql

    home.mkdir(parents=True, exist_ok=True)
    protect(home, directory=True)
    state_file = home / "bootstrap.json"
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        if state["port"] != requested_port:
            raise RuntimeError("Runtime uses a different port; select a separate --home")
    else:
        if (home / "data" / "PG_VERSION").exists():
            raise RuntimeError("Existing cluster has no bootstrap record; refusing to overwrite it")
        state = {
            "port": requested_port,
            "admin_password": secrets.token_urlsafe(36),
            "app_password": secrets.token_urlsafe(36),
            "admin_token": secrets.token_urlsafe(48),
        }
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", requested_port)) == 0:
                raise RuntimeError("Requested port is already in use; choose another with --port")
        state_file.write_text(json.dumps(state), encoding="utf-8")
        protect(state_file)
    if not Path(executable(home, "initdb")).is_file():
        archive = home / f"postgresql-{VERSION}.zip"
        if not archive.exists():
            print("Downloading official PostgreSQL 16 Windows binaries (~317 MiB)...", flush=True)
            partial = archive.with_suffix(".zip.part")
            with (
                urllib.request.urlopen(DOWNLOAD, timeout=120) as response,
                partial.open("wb") as out,
            ):
                shutil.copyfileobj(response, out)
            partial.replace(archive)
        with archive.open("rb") as downloaded:
            digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
        print("Extracting PostgreSQL runtime...", flush=True)
        with zipfile.ZipFile(archive) as bundle:
            for entry in bundle.infolist():
                if not entry.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                    continue
                target = (home / entry.filename).resolve()
                if not target.is_relative_to(home.resolve()):
                    raise RuntimeError("Unsafe archive path")
                bundle.extract(entry, home)
        (home / "download-manifest.json").write_text(
            json.dumps(
                {
                    "source": DOWNLOAD,
                    "version": VERSION,
                    "sha256": digest,
                    "verification": "HTTPS download; locally recorded digest",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    if not (home / "data" / "PG_VERSION").exists():
        password_file = home / "initdb-password.tmp"
        password_file.write_text(state["admin_password"], encoding="utf-8")
        protect(password_file)
        try:
            run(
                [
                    executable(home, "initdb"),
                    "-D",
                    str(home / "data"),
                    "-U",
                    "firewatch_bootstrap",
                    "--encoding=UTF8",
                    "--locale=C",
                    "--auth=scram-sha-256",
                    "--pwfile",
                    str(password_file),
                ]
            )
        finally:
            password_file.unlink(missing_ok=True)
        with (home / "data" / "postgresql.conf").open("a", encoding="utf-8") as config:
            config.write(
                f"\n# FireWatch native development only\nlisten_addresses = '127.0.0.1'\n"
                f"port = {state['port']}\ntimezone = 'UTC'\nlog_timezone = 'UTC'\n"
                "password_encryption = 'scram-sha-256'\n"
            )
    start(home, state["port"])
    with psycopg.connect(
        host="127.0.0.1",
        port=state["port"],
        user="firewatch_bootstrap",
        dbname="postgres",
        password=state["admin_password"],
        autocommit=True,
        connect_timeout=5,
    ) as connection:
        if not connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", ("firewatch",)
        ).fetchone():
            connection.execute(
                sql.SQL(
                    "CREATE ROLE firewatch LOGIN NOSUPERUSER NOCREATEDB "
                    "NOCREATEROLE NOREPLICATION PASSWORD {}"
                ).format(sql.Literal(state["app_password"]))
            )
        for database in ("firewatch", "firewatch_test"):
            if not connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (database,)
            ).fetchone():
                connection.execute(
                    sql.SQL("CREATE DATABASE {} OWNER firewatch").format(sql.Identifier(database))
                )
            connection.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database)
                )
            )
            connection.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO firewatch").format(
                    sql.Identifier(database)
                )
            )
    environment = ROOT / ".env"
    if not environment.exists():
        template = (ROOT / ".env.example").read_text(encoding="utf-8")
        template = template.replace(
            "REPLACE_ME@localhost:5432", f"{state['app_password']}@127.0.0.1:{state['port']}"
        )
        template = template.replace(
            "POSTGRES_PASSWORD=REPLACE_ME", f"POSTGRES_PASSWORD={state['app_password']}"
        )
        template = template.replace(
            "ADMIN_API_TOKEN=REPLACE_WITH_RANDOM_SECRET", f"ADMIN_API_TOKEN={state['admin_token']}"
        )
        environment.write_text(template, encoding="utf-8", newline="\n")
        protect(environment)
        print("Generated protected .env with random local credentials; values were not displayed.")
    else:
        print("Existing .env preserved. Run firewatch check-config and firewatch db-check.")
    print("Dedicated firewatch role and firewatch/firewatch_test databases provisioned.")
    return state


def main() -> int:
    if sys.platform != "win32":
        print(
            "This helper targets native Windows; Linux CI uses its PostgreSQL service.",
            file=sys.stderr,
        )
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "start", "stop", "status", "backup"])
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument(
        "--home",
        type=Path,
        default=Path(os.environ["LOCALAPPDATA"]) / "FireWatch" / "PostgreSQL16",
        help="Private runtime/data directory; use a separate home for an isolated instance",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535")
    home = args.home.resolve()
    try:
        if args.action == "setup":
            setup(home, args.port)
            return 0
        state = json.loads((home / "bootstrap.json").read_text(encoding="utf-8"))
        if args.action == "start":
            start(home, state["port"])
        elif args.action == "stop":
            run([executable(home, "pg_ctl"), "stop", "-D", str(home / "data"), "-m", "fast", "-w"])
            print("PostgreSQL stopped cleanly; persistent data retained.")
        elif args.action == "status":
            run([executable(home, "pg_isready"), "-h", "127.0.0.1", "-p", str(state["port"])])
            print(f"PostgreSQL ready on 127.0.0.1:{state['port']}")
        elif args.action == "backup":
            from datetime import UTC, datetime

            directory = home / "backups"
            directory.mkdir(exist_ok=True)
            destination = directory / f"firewatch-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.dump"
            environment = dict(os.environ, PGPASSWORD=state["app_password"])
            result = subprocess.run(
                [
                    executable(home, "pg_dump"),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(state["port"]),
                    "-U",
                    "firewatch",
                    "-d",
                    "firewatch",
                    "-Fc",
                    "-f",
                    str(destination),
                ],
                env=environment,
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                raise RuntimeError("pg_dump failed")
            print(f"Local backup saved: {destination}")
        return 0
    except (
        OSError,
        RuntimeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
        psycopg.Error,
    ) as exc:
        print(
            f"Native PostgreSQL action failed: {type(exc).__name__}. "
            f"Inspect the protected runtime directory: {home}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
