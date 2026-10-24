"""Bounded startup recovery and command execution without provider calls or leaked errors."""

import threading

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from firewatch import runtime
from firewatch.config import Settings


def settings(**kwargs):
    return Settings(
        database_url="postgresql+psycopg://test:test-password@localhost:1/firewatch_test",
        admin_api_token="test-only-" + "x" * 40,
        _env_file=None,
        **kwargs,
    )


@pytest.mark.parametrize("failures", [0, 2])
def test_startup_recovers_and_closes_pool(monkeypatch, failures):
    closed, attempts = [], []
    monkeypatch.setattr(
        runtime, "Database", lambda _: type("DB", (), {"close": lambda _: closed.append(True)})()
    )

    def check(*args, **kwargs):
        attempts.append(kwargs["schema"])
        if len(attempts) <= failures:
            raise OperationalError("private-provider-or-dsn-must-not-leak", {}, Exception())

    monkeypatch.setattr(runtime, "check_database", check)
    assert runtime.wait_for_database(
        settings(startup_retry_seconds=0.1), schema=True, stop=threading.Event()
    )
    assert attempts == [True] * (failures + 1) and closed == [True]


def test_startup_deadline_and_stop_never_execute_command(monkeypatch, capsys):
    monkeypatch.setattr(runtime, "get_settings", settings)
    monkeypatch.setattr(runtime, "wait_for_database", lambda *a, **k: False)
    monkeypatch.setattr(runtime.signal, "signal", lambda *a: None)
    monkeypatch.setattr(runtime.os, "execvp", lambda *a: pytest.fail("Executed before DB ready"))
    assert runtime.main(["uvicorn", "firewatch.main:app"]) == 1
    assert "database/schema unavailable" in capsys.readouterr().err


def test_retry_budget_expiry_closes_pool_without_an_extra_attempt(monkeypatch):
    closed, calls = [], []
    times = iter([0, 0, 1])
    monkeypatch.setattr(runtime.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(
        runtime, "Database", lambda _: type("DB", (), {"close": lambda _: closed.append(True)})()
    )

    def unavailable(*args, **kwargs):
        calls.append(True)
        raise RuntimeError("schema missing")

    monkeypatch.setattr(runtime, "check_database", unavailable)
    assert not runtime.wait_for_database(
        settings(startup_timeout_seconds=1), schema=True, stop=threading.Event()
    )
    assert calls == [True] and closed == [True]


def test_database_only_is_explicit_and_exec_preserves_argument_boundaries(monkeypatch):
    selected = []
    monkeypatch.setattr(runtime, "get_settings", settings)
    monkeypatch.setattr(runtime.signal, "signal", lambda *a: None)
    monkeypatch.setattr(
        runtime, "wait_for_database", lambda *a, **k: selected.append(k["schema"]) or True
    )

    def execute(program, arguments):
        assert program == "alembic" and arguments == ["alembic", "upgrade", "head"]
        raise SystemExit(0)

    monkeypatch.setattr(runtime.os, "execvp", execute)
    with pytest.raises(SystemExit):
        runtime.main(["--database-only", "alembic", "upgrade", "head"])
    assert selected == [False]


def test_local_chain_host_override_is_explicit_and_does_not_relax_native_default():
    values = dict(
        chain_enabled=True,
        chain_rpc_url="http://anvil:8545",
        alert_registry_address="0x" + "1" * 40,
    )
    with pytest.raises(ValidationError):
        settings(**values)
    configured = settings(**values, chain_local_rpc_hosts=["127.0.0.1", "anvil"])
    assert configured.chain_private_key is None
