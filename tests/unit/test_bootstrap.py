"""Bootstrap behavior and configuration safety; no live providers required."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from typer.testing import CliRunner

from firewatch.cli import app as cli
from firewatch.config import ConfigurationError, Settings, get_settings
from firewatch.db.session import Database
from firewatch.main import create_app

TOKEN = "test-only-operator-token-" + "x" * 32
DSN = "postgresql+psycopg://firewatch:test-only-password@localhost:1/firewatch"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def valid_settings(**kwargs):
    return Settings(database_url=DSN, admin_api_token=TOKEN, _env_file=None, **kwargs)


def test_missing_configuration_is_clear_and_safe():
    with pytest.raises(ConfigurationError, match="admin_api_token, database_url"):
        get_settings()


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///firewatch.db",
        "postgresql+psycopg://user:REPLACE_ME@localhost/db",
        "postgresql+psycopg://localhost/db",
        "not-a-url",
    ],
)
def test_invalid_database_url_rejected_without_secrets(url):
    with pytest.raises(ValidationError) as error:
        Settings(database_url=url, admin_api_token=TOKEN, _env_file=None)
    assert url not in str(error.value)


def test_env_file_and_environment_precedence(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"DATABASE_URL={DSN}\nADMIN_API_TOKEN={TOKEN}\nREFRESH_INTERVAL_MINUTES=30\n")
    monkeypatch.setenv("REFRESH_INTERVAL_MINUTES", "20")
    assert Settings().refresh_interval_minutes == 20


@pytest.mark.parametrize("token", ["", "REPLACE_WITH_RANDOM_SECRET", "short"])
def test_operator_token_cannot_be_missing_or_placeholder(token):
    with pytest.raises(ValidationError):
        Settings(database_url=DSN, admin_api_token=token, _env_file=None)


def test_secret_repr_and_safe_error(monkeypatch):
    settings = valid_settings()
    assert TOKEN not in repr(settings)
    assert "test-only-password" not in repr(settings)
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ADMIN_API_TOKEN", "secret-but-too-short")
    with pytest.raises(ConfigurationError) as error:
        get_settings()
    assert "secret-but-too-short" not in str(error.value)
    assert "test-only-password" not in str(error.value)


def test_optional_sources_not_needed_until_ingestion():
    assert valid_settings(firms_map_key="REPLACE_ME").firms_map_key is None
    with pytest.raises(ValidationError, match="ALERT_REGISTRY_ADDRESS"):
        valid_settings(chain_enabled=True)


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.example.com",
        "ftp://example.com",
        "https://u:p@example.com",
        "https://example.com/path",
    ],
)
def test_invalid_cors_origin_rejected(origin):
    with pytest.raises(ValidationError):
        valid_settings(cors_origins=[origin])


def test_liveness_does_not_connect_to_database(monkeypatch):
    def unexpected_connection(*args, **kwargs):
        raise AssertionError("Liveness must not query the database")

    monkeypatch.setattr(Database, "check_connection", unexpected_connection)
    with TestClient(create_app(valid_settings())) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_configured_cors():
    with TestClient(create_app(valid_settings(cors_origins=["https://example.com"]))) as client:
        response = client.get("/health/live", headers={"Origin": "https://example.com"})
        assert response.headers["access-control-allow-origin"] == "https://example.com"
        denied = client.get("/health/live", headers={"Origin": "https://other.example"})
        assert "access-control-allow-origin" not in denied.headers


def test_cli_help_without_secrets():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "db-check" in result.stdout


def test_cli_invalid_configuration_has_nonzero_exit():
    result = CliRunner().invoke(cli, ["check-config"])
    assert result.exit_code == 2
    assert "Invalid or missing configuration" in result.output


def test_cli_config_does_not_print_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    result = CliRunner().invoke(cli, ["check-config"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["status"] == "valid"
    assert TOKEN not in result.stdout
    assert DSN not in result.stdout


def test_application_rejects_invalid_config_at_startup():
    with pytest.raises(ConfigurationError), TestClient(create_app()):
        pass


def test_default_app_uses_environment_cors(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    monkeypatch.setenv("CORS_ORIGINS", '["https://example.com"]')
    with TestClient(create_app()) as client:
        response = client.get("/health/live", headers={"Origin": "https://example.com"})
        assert response.headers["access-control-allow-origin"] == "https://example.com"


def test_db_cli_failure_is_safe_and_closes_pool(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    closed = []

    def fail_check(self):
        raise SQLAlchemyError("sensitive-driver-message-with-password")

    def close(self):
        closed.append(True)
        self.engine.dispose()

    monkeypatch.setattr(Database, "check_connection", fail_check)
    monkeypatch.setattr(Database, "close", close)
    result = CliRunner().invoke(cli, ["db-check"])
    assert result.exit_code == 1
    assert "Database unavailable" in result.output
    assert "sensitive-driver-message" not in result.output
    assert TOKEN not in result.output
    assert closed == [True]
