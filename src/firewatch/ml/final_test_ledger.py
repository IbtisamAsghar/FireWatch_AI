"""Durable single-use final-test governance, keyed by region/target/held-out period."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class FinalTestLedger:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS claims (holdout_key TEXT PRIMARY KEY, "
                "prepared_sha256 TEXT NOT NULL, output_path TEXT NOT NULL, "
                "state TEXT NOT NULL, "
                "claimed_at TEXT NOT NULL, result_sha256 TEXT)"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM claims WHERE holdout_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def claim(self, key: str, prepared_sha256: str, output: Path) -> dict[str, Any]:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO claims VALUES (?,?,?,'claimed',?,NULL)",
                    (key, prepared_sha256, str(output.resolve()), datetime.now(UTC).isoformat()),
                )
        except sqlite3.IntegrityError:
            raise ValueError(
                "Final holdout was already claimed; no second experiment is permitted"
            ) from None
        result = self.get(key)
        assert result is not None
        return result

    def complete(self, key: str, prepared_sha256: str, output: Path, result_sha256: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                (
                    "UPDATE claims SET state='complete',result_sha256=? WHERE holdout_key=? "
                    "AND prepared_sha256=? AND output_path=? AND state IN ('claimed','failed')"
                ),
                (result_sha256, key, prepared_sha256, str(output.resolve())),
            )
            if cursor.rowcount != 1:
                raise ValueError("Final-test completion does not match the durable claim")

    def fail(self, key: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE claims SET state='failed' WHERE holdout_key=? AND state='claimed'", (key,)
            )
