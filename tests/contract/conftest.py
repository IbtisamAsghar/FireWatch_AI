"""Contract acceptance uses a fresh migrated synthetic demo schema, never app data."""

import os

import pytest
from fastapi.testclient import TestClient

from firewatch.config import Settings
from scripts.demo_api import create_demo_app


@pytest.fixture(scope="module")
def demo_settings():
    url = os.getenv("FIREWATCH_TEST_DATABASE_URL")
    if not url:
        pytest.skip("FIREWATCH_TEST_DATABASE_URL required for PostgreSQL contract acceptance")
    return Settings(
        database_url=url,
        admin_api_token="contract-test-operator-" + "x" * 40,
        app_env="test",
        public_reads_enabled=True,
        _env_file=None,
    )


@pytest.fixture(scope="module")
def demo_client(demo_settings):
    with TestClient(create_demo_app(demo_settings)) as client:
        yield client
