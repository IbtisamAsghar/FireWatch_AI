"""Weather fill preserves historical identity and stops safely at provider budgets."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from firewatch.ml.backfill_weather import fill_weather
from firewatch.sources.open_meteo import WeatherRequest, weather_cell
from firewatch.sources.weather_budget import WeatherError


def requests():
    return [
        WeatherRequest(
            cells=[weather_cell(39.5, -121.5)],
            start=datetime(2022, 5, 1, tzinfo=UTC) + timedelta(days=i),
            end=datetime(2022, 5, 1, 23, tzinfo=UTC) + timedelta(days=i),
            product="historical_forecast",
            model="gfs_seamless",
            source_version="weather-v1",
            mode="replay",
        )
        for i in range(3)
    ]


def test_weather_fill_is_bounded_and_retries_same_offset_after_failure(monkeypatch):
    import firewatch.ml.backfill_weather as module

    calls = []

    def fetch(engine, request, *, client):
        calls.append(request)
        if len(calls) == 2:
            raise WeatherError("synthetic provider budget exhausted")

    monkeypatch.setattr(module, "get_weather", fetch)
    result = fill_weather(Mock(), requests(), Mock(), max_requests=3)
    assert result["status"] == "paused"
    assert result["next_offset"] == result["completed_requests"] == 1
    assert len(calls) == 2
    assert all(r.product == "historical_forecast" and r.mode == "replay" for r in calls)


def test_successful_weather_fill_does_not_exceed_requested_batch(monkeypatch):
    import firewatch.ml.backfill_weather as module

    fetch = Mock()
    monkeypatch.setattr(module, "get_weather", fetch)
    result = fill_weather(Mock(), requests(), Mock(), offset=1, max_requests=1)
    assert result["status"] == "bounded_batch_complete" and result["next_offset"] == 2
    assert fetch.call_count == 1


@pytest.mark.parametrize("offset,bound", [(-1, 1), (4, 1), (0, 0), (0, 21)])
def test_invalid_weather_fill_bounds_fail_before_provider_calls(offset, bound):
    with pytest.raises(ValueError):
        fill_weather(Mock(), requests(), Mock(), offset=offset, max_requests=bound)
