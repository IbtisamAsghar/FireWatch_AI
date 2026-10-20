"""Migration tests use disposable schemas and never downgrade application tables."""

import os
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.session import get_session
from firewatch.main import create_app

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "test-only-operator-" + "x" * 40


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


@pytest.fixture
def api(empty_engine, tmp_path):
    from test_schema import T

    migrate(empty_engine)
    settings = Settings(
        database_url="postgresql+psycopg://test:password@localhost:1/firewatch_test",
        admin_api_token=TOKEN,
        model_dir=tmp_path,
        _env_file=None,
    )
    app = create_app(settings)

    def sessions():
        with Session(empty_engine) as session:
            yield session

    app.dependency_overrides[get_session] = sessions
    with TestClient(app, raise_server_exceptions=False) as client:
        app.state.clock = lambda: T + timedelta(hours=5)
        yield client, empty_engine, settings
