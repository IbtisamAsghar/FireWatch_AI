"""Live provider checks require explicit opt-in and never expose raw failures."""

import json
import sys

import pytest

from scripts import live_provider_smoke


@pytest.mark.parametrize(
    "args,key", [([], "synthetic-firms-test-key-1234567890"), (["--live"], "")]
)
def test_live_smoke_requires_flag_and_explicit_environment_key(monkeypatch, args, key, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["live_provider_smoke", *args])
    monkeypatch.setenv("FIRMS_MAP_KEY", key)
    monkeypatch.setattr(
        live_provider_smoke,
        "FirmsClient",
        lambda *a, **k: pytest.fail("Provider called without authorization"),
    )
    with pytest.raises(SystemExit) as error:
        live_provider_smoke.main()
    assert error.value.code == 2
    assert not (tmp_path / "artifacts").exists()


def test_live_smoke_saves_only_safe_failure_status(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["live_provider_smoke", "--live"])
    monkeypatch.setenv("FIRMS_MAP_KEY", "synthetic-firms-test-key-1234567890")

    def unavailable(*args, **kwargs):
        raise RuntimeError("sensitive-provider-response-must-not-be-printed")

    monkeypatch.setattr(live_provider_smoke, "FirmsClient", unavailable)
    assert live_provider_smoke.main() == 1
    report = (tmp_path / "artifacts/live-provider-smoke.json").read_text()
    assert json.loads(report)["firms"] == "failed"
    assert "sensitive-provider" not in report + capsys.readouterr().out
