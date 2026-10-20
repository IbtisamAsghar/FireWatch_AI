"""Demonstration lifecycle never changes public application tables or leaves schemas."""

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from scripts.demo_api import demo_schema
from scripts.demo_data import seed_demo

pytestmark = pytest.mark.integration


def test_clean_migration_seed_is_idempotent_and_only_owned_schema_is_removed(demo_settings):
    control = create_engine(demo_settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        before = set(inspect(control).get_schema_names())
        with demo_schema(demo_settings) as engine:
            with Session(engine) as session:
                schema = session.scalar(text("SELECT current_schema()"))
                assert schema.startswith("fw_demo_")
                assert session.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
                assert session.scalar(text("SELECT count(*) FROM runs")) == 4
                assert session.scalar(text("SELECT count(*) FROM cluster_snapshots")) == 3
                with session.begin_nested():
                    seed_demo(session)
                assert session.scalar(text("SELECT count(*) FROM cluster_snapshots")) == 3
        assert set(inspect(control).get_schema_names()) == before
        with Session(control) as session:
            with pytest.raises(ValueError, match="disposable"):
                seed_demo(session)
    finally:
        control.dispose()


def test_failed_demo_seed_still_cleans_its_schema(demo_settings, monkeypatch):
    control = create_engine(demo_settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        before = set(inspect(control).get_schema_names())

        def fail_seed(session):
            raise RuntimeError("synthetic setup failure")

        monkeypatch.setattr("scripts.demo_api.seed_demo", fail_seed)
        with pytest.raises(RuntimeError, match="synthetic setup failure"):
            with demo_schema(demo_settings):
                pytest.fail("Fixture must fail before serving")
        assert set(inspect(control).get_schema_names()) == before
    finally:
        control.dispose()
