"""Startup gates use real schema state, without creating/migrating tables implicitly."""

from contextlib import contextmanager

import pytest
from conftest import migrate
from sqlalchemy.orm import Session

from firewatch.runtime import check_database

pytestmark = pytest.mark.integration


def test_schema_gate_requires_explicit_migration(empty_engine):
    class DB:
        @contextmanager
        def session(self):
            with Session(empty_engine) as session:
                yield session

    check_database(DB(), schema=False)
    with pytest.raises(RuntimeError, match="migrations are required"):
        check_database(DB(), schema=True)
    migrate(empty_engine)
    check_database(DB(), schema=True)
