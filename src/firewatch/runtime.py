"""Container startup/readiness: bounded DB retries, explicit schema gate, then exec."""

import argparse
import os
import signal
import sys
import threading
import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from firewatch.config import ConfigurationError, Settings, get_settings
from firewatch.db.base import SCHEMA_REVISION
from firewatch.db.session import Database


def check_database(database: Database, *, schema: bool) -> None:
    with database.session() as session:
        session.execute(text("SELECT 1"))
        if schema:
            if session.scalar(text("SELECT to_regclass('alembic_version')")) is None:
                raise RuntimeError("Database migrations are required")
            revisions = set(session.scalars(text("SELECT version_num FROM alembic_version")))
            if revisions != {SCHEMA_REVISION}:
                raise RuntimeError("Database migration revision differs from this release")


def wait_for_database(settings: Settings, *, schema: bool, stop: threading.Event) -> bool:
    deadline = time.monotonic() + settings.startup_timeout_seconds
    database = Database(settings)
    try:
        while not stop.is_set() and time.monotonic() < deadline:
            try:
                check_database(database, schema=schema)
                return True
            except (SQLAlchemyError, RuntimeError):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                stop.wait(min(settings.startup_retry_seconds, remaining))
        return False
    finally:
        database.close()


def health(settings: Settings) -> bool:
    database = Database(settings)
    try:
        check_database(database, schema=True)
        return (
            settings.raw_data_dir.is_dir()
            and os.access(settings.raw_data_dir, os.W_OK)
            and settings.model_dir.is_dir()
            and os.access(settings.model_dir, os.R_OK)
            and settings.replay_bundle_dir.is_dir()
            and os.access(settings.replay_bundle_dir, os.R_OK)
        )
    finally:
        database.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-only", action="store_true", help="For explicit migration commands"
    )
    parser.add_argument("--health", action="store_true", help="Worker DB/schema/storage health")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        if args.health:
            return 0 if health(settings) else 1
        if not args.command:
            parser.error("Supply the application command")
        stop = threading.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            signal.signal(signum, lambda *_: stop.set())
        if not wait_for_database(settings, schema=not args.database_only, stop=stop):
            print(
                "Startup stopped: database/schema unavailable within startup budget.",
                file=sys.stderr,
            )
            return 1
        if stop.is_set():
            return 1
        os.execvp(args.command[0], args.command)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
    except (SQLAlchemyError, RuntimeError, OSError):
        print("Runtime check failed; inspect database, migrations and storage.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
