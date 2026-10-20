"""Real PostgreSQL pooling and transaction acceptance checks."""

import os
from uuid import uuid4

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, func, select

from firewatch.config import Settings
from firewatch.db.session import Database

pytestmark = pytest.mark.integration


@pytest.fixture
def database():
    url = os.getenv("FIREWATCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set FIREWATCH_TEST_DATABASE_URL to a disposable PostgreSQL database")
    settings = Settings(
        database_url=url, admin_api_token="integration-test-" + "x" * 40, _env_file=None
    )
    database = Database(settings)
    yield database
    database.close()


def test_connection_is_utc_and_postgresql_16(database):
    result = database.check_connection()
    assert result["timezone"] == "UTC"
    assert result["version"].startswith("16.")


def test_commit_rollback_and_request_scope(database):
    metadata = MetaData()
    table = Table(
        "bootstrap_test_" + uuid4().hex, metadata, Column("id", Integer, primary_key=True)
    )
    metadata.create_all(database.engine)
    try:
        with database.transaction() as session:
            session.execute(table.insert().values(id=1))
        with pytest.raises(RuntimeError), database.transaction() as session:
            session.execute(table.insert().values(id=2))
            raise RuntimeError("Simulated failed unit of work")
        # Closing a request/read session must never commit an accidental write.
        with database.session() as session:
            session.execute(table.insert().values(id=3))
        with database.session() as session:
            assert session.scalars(select(table.c.id)).all() == [1]
        # Pool remains usable after exceptions and rollback.
        with database.transaction() as session:
            session.execute(table.insert().values(id=4))
        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(table)) == 2
    finally:
        metadata.drop_all(database.engine)
