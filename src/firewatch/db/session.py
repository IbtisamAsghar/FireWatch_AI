"""Synchronous PostgreSQL sessions with explicit write transaction ownership."""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Request
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from firewatch.config import Settings


class Database:
    """One bounded connection pool per API/worker process; constructed without I/O."""

    def __init__(self, settings: Settings) -> None:
        self.engine = create_engine(
            settings.database_url.get_secret_value(),
            echo=False,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_recycle=1800,
            connect_args={
                "connect_timeout": settings.db_connect_timeout_seconds,
                "application_name": "firewatch",
                "options": "-c timezone=UTC "
                f"-c statement_timeout={settings.db_statement_timeout_ms}",
            },
        )
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Read/request scope: no automatic commit; close rolls back pending work."""
        with self.session_factory() as session:
            yield session

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        """Short write unit: commit on success, rollback on every exception."""
        with self.session_factory.begin() as session:
            yield session

    def check_connection(self) -> dict[str, Any]:
        with self.session() as session:
            row = (
                session.execute(
                    text(
                        "SELECT current_database() AS database, current_user AS role, "
                        "current_setting('server_version') AS version, "
                        "current_setting('TimeZone') AS timezone"
                    )
                )
                .mappings()
                .one()
            )
            return dict(row)

    def close(self) -> None:
        self.engine.dispose()


def get_session(request: Request) -> Generator[Session, None, None]:
    """FastAPI dependency; mutations must explicitly own their transaction."""
    database: Database = request.app.state.database
    with database.session() as session:
        yield session
