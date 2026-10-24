"""Run one dedicated PostgreSQL-serialized anchor signer, independent of scoring."""

import argparse
import logging
import signal
import threading

from sqlalchemy.exc import SQLAlchemyError

from firewatch.config import get_settings
from firewatch.db.session import Database
from firewatch.evidence.anchor_delivery import OwnerLost, process_one
from firewatch.evidence.chain_rpc import ChainError
from firewatch.logging import install_redaction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process at most one due outbox entry")
    args = parser.parse_args(argv)
    settings = get_settings()
    if not settings.chain_enabled:
        print("Optional chain anchoring is disabled.")
        return 0
    close_redaction = install_redaction(settings)
    logging.basicConfig(level=logging.INFO)
    database = Database(settings)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                process_one(database.engine, settings)
            except (ChainError, SQLAlchemyError, OwnerLost, ValueError, OSError):
                logging.getLogger("firewatch.anchor_worker").error(
                    "Anchor worker unavailable; saved evidence remains available."
                )
                if args.once:
                    return 1
            if args.once:
                return 0
            stop.wait(settings.chain_poll_seconds)
    finally:
        database.close()
        close_redaction()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
