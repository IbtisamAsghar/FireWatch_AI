"""Migration tests use disposable schemas and never downgrade application tables."""

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def isolated_schema():
    value = os.getenv("FIREWATCH_TEST_DATABASE_URL")
    if not value:
        pytest.skip("FIREWATCH_TEST_DATABASE_URL is required for migration tests")
    url = make_url(value)
    if not (url.database or "").endswith("_test"):
        pytest.fail("Migration tests require a disposable database whose name ends in _test")
    schema = "fw_test_" + uuid4().hex
    control = create_engine(url, hide_parameters=True)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with control.begin() as connection:
            assert schema.startswith("fw_test_") and len(schema) == 40
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def migrate(engine, revision="head", downgrade=False, check=False):
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        if check:
            command.check(config)
        elif downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)


@pytest.fixture
def empty_engine():
    with isolated_schema() as engine:
        yield engine


@pytest.fixture(scope="module")
def schema_engine():
    with isolated_schema() as engine:
        migrate(engine)
        yield engine


@pytest.fixture
def session(schema_engine):
    with schema_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection) as session:
            yield session
        transaction.rollback()
