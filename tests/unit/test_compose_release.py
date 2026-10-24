"""Release sequencing, credential isolation and no destructive default shutdown."""

from pathlib import Path

import pytest
import yaml

from scripts import compose_stack

ROOT = Path(__file__).parents[2]


def generated(tmp_path, monkeypatch):
    from scripts import native_postgres

    monkeypatch.setattr(native_postgres, "protect", lambda _: None)
    path = tmp_path / "compose.env"
    assert compose_stack.init_environment(path)
    return path


def test_compose_credentials_are_separate_non_overwriting_and_explicit(tmp_path, monkeypatch):
    native = tmp_path / ".env"
    native.write_text("native-must-remain-unchanged")
    path = generated(tmp_path, monkeypatch)
    original = path.read_bytes()
    assert not compose_stack.init_environment(path)
    assert path.read_bytes() == original and native.read_text() == "native-must-remain-unchanged"
    monkeypatch.setenv("POSTGRES_PASSWORD", "native-environment-must-not-override-compose")
    values = compose_stack.environment(path)
    assert values["POSTGRES_PASSWORD"] != "native-environment-must-not-override-compose"
    assert values["POSTGRES_PASSWORD"] != values["APP_DATABASE_PASSWORD"]
    assert values["FIRMS_MAP_KEY"] == "" and values["CHAIN_ENABLED"] == "false"


def test_release_quiesces_writers_migrates_once_and_starts_after_seed(tmp_path, monkeypatch):
    stack = compose_stack.Stack(generated(tmp_path, monkeypatch), "fwtest-release")
    calls = []
    monkeypatch.setattr(stack, "preflight", lambda: None)
    monkeypatch.setattr(stack, "run", lambda *a, **k: calls.append(a) or "")
    stack.up(build=True)
    assert calls[0] == ("build",)
    assert calls[1][:2] == ("stop", "api")
    assert sum(command[-1] == "migrate" for command in calls) == 1
    assert calls[-2][-1] == "seed" and calls[-1][-2:] == ("api", "worker")
    assert not any("--volumes" in command for command in calls)


@pytest.mark.parametrize("project", ["../other", "", "fw;delete", "UPPER"])
def test_project_names_cannot_escape_compose_namespace(tmp_path, monkeypatch, project):
    with pytest.raises(ValueError):
        compose_stack.Stack(generated(tmp_path, monkeypatch), project)


def test_reviewed_compose_contract_preserves_boundaries():
    spec = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = spec["services"]
    assert "ports" not in services["db"] and "ports" not in services["anvil"]
    assert services["db"]["image"].startswith("postgres:16.")
    assert services["api"]["user"] == services["worker"]["user"] == "10001:10001"
    assert services["api"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert all(v.endswith(":ro") for v in services["api"]["volumes"])
    assert "CHAIN_PRIVATE_KEY" not in services["api"]["environment"]
    assert "POSTGRES_PASSWORD" not in services["api"]["environment"]
    assert "FIRMS_MAP_KEY" not in services["api"]["environment"]
    assert services["migrate"]["profiles"] == ["tools"]
    assert services["worker"]["command"] == ["python", "-m", "firewatch.worker"]
    assert services["anvil"]["profiles"] == services["anchor-worker"]["profiles"] == ["chain"]
