"""Synthetic provider responses; not real weather evidence."""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from typer.testing import CliRunner
from weather_factory import NOW, T, payload, request, series

from firewatch.cli import app
from firewatch.processing.weather import weather_at_cutoff
from firewatch.sources.open_meteo import (
    HOUR,
    VARIABLES,
    HistoricalForecastAdapter,
    LiveWeatherAdapter,
    OpenMeteoClient,
    ReanalysisAdapter,
    parse_weather,
    weather_cell,
)
from firewatch.sources.weather_budget import WeatherBudget, WeatherError


def client(tmp_path, handler, *, cls=OpenMeteoClient):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return cls(
        tmp_path / "raw",
        WeatherBudget(tmp_path / "budget.sqlite"),
        client=http,
        now=lambda: NOW,
        sleep=lambda _: None,
    )


@pytest.mark.parametrize(
    "lat,lon,center",
    [
        (0, 0, (0.05, 0.05)),
        (-0.1, -0.1, (-0.05, -0.05)),
        (90, 180, (89.95, 179.95)),
        (-90, -180, (-89.95, -179.95)),
        (39.4, -121.4, (39.45, -121.35)),
    ],
)
def test_grid_boundaries(lat, lon, center):
    cell = weather_cell(lat, lon)
    assert (cell.latitude, cell.longitude) == center
    assert weather_cell(*center) == cell


@pytest.mark.parametrize("lat,lon", [(91, 0), (0, -181), (float("nan"), 0), (0, float("inf"))])
def test_bad_grid(lat, lon):
    with pytest.raises(ValueError):
        weather_cell(lat, lon)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(start=T.replace(tzinfo=None)),
        dict(end=T - HOUR),
        dict(end=T + timedelta(days=8)),
        dict(start=T + timedelta(minutes=1)),
        dict(product="live"),
        dict(product="reanalysis"),
        dict(model="era5"),
        dict(source_version="unsafe/path"),
        dict(cells=[weather_cell(0, 0)] * 2),
    ],
)
def test_request_bounds(overrides):
    with pytest.raises(ValueError):
        request(**overrides)


def test_typed_units_coordinates_and_interpolation():
    row = series().records[0]
    assert row.requested_latitude == 39.45 and row.latitude == 39.46
    assert row.units["wind_ms"] == "m/s"
    assert "retrospective_weather" in row.quality_flags
    assert "provider_interpolation_unknown" in row.quality_flags


@pytest.mark.parametrize(
    "damage",
    [
        "unit",
        "zone",
        "offset",
        "shape",
        "duplicate",
        "order",
        "coordinate",
        "error",
        "missing",
        "location",
        "timestamp",
    ],
)
def test_bad_provider_schema(damage):
    req = request()
    data = payload(req)
    if damage == "unit":
        data["hourly_units"]["wind_speed_10m"] = "km/h"
    if damage == "zone":
        data["timezone"] = "Europe/Berlin"
    if damage == "offset":
        data["utc_offset_seconds"] = 3600
    if damage == "shape":
        data["hourly"]["precipitation"].pop()
    if damage == "duplicate":
        data["hourly"]["time"][1] = data["hourly"]["time"][0]
    if damage == "order":
        data["hourly"]["time"].reverse()
    if damage == "coordinate":
        data["latitude"] = 0
    if damage == "error":
        data["error"] = True
    if damage == "missing":
        del data["hourly"]
    if damage == "location":
        data = [data, data]
    if damage == "timestamp":
        data["hourly"]["time"][0] = "2024-07-25T00:15"
    with pytest.raises(WeatherError):
        parse_weather(data, req, NOW, uuid4(), "v1")


@pytest.mark.parametrize(
    "variable,value",
    [
        ("temperature_2m", -999),
        ("relative_humidity_2m", 101),
        ("wind_speed_10m", -1),
        ("wind_gusts_10m", True),
        ("precipitation", float("nan")),
        ("precipitation", None),
    ],
)
def test_invalid_values_are_null(variable, value):
    req = request()
    data = payload(req)
    data["hourly"][variable][0] = value
    row = parse_weather(data, req, NOW, uuid4(), "v1").records[0]
    assert getattr(row, VARIABLES[variable][0]) is None
    assert any(VARIABLES[variable][0] in f for f in row.quality_flags)


def test_missing_hour_no_imputation():
    req = request()
    data = payload(req)
    for values in data["hourly"].values():
        values.pop(10)
    parsed = parse_weather(data, req, NOW, uuid4(), "v1")
    assert len(parsed.records) == 25
    assert parsed.records[10].temperature_c is None
    assert "weather_hour_missing" in parsed.records[10].quality_flags
    result = weather_at_cutoff(parsed, req.end)
    assert result.precipitation_24h_mm is None


def test_rainfall_exact_boundaries_and_future_exclusion():
    req = request(end=T + timedelta(hours=25))
    data = series(req)
    data.records[0].precipitation_mm = 1000
    data.records[-1].temperature_c = 100
    result = weather_at_cutoff(data, T + timedelta(hours=24, minutes=30))
    assert result.precipitation_24h_mm == 24
    assert result.record.valid_at == T + 24 * HOUR
    assert result.record.temperature_c == 1
    assert result.weather_age_hours == 0.5
    assert result.precipitation_interval_end == T + 24 * HOUR


def test_stale_missing_and_zero_rain():
    req = request()
    data = series(req)
    for row in data.records:
        row.precipitation_mm = 0
    assert weather_at_cutoff(data, req.end).precipitation_24h_mm == 0
    assert "weather_stale" in weather_at_cutoff(data, req.end + 4 * HOUR).quality_flags
    data.records = []
    assert "weather_missing" in weather_at_cutoff(data, req.end).quality_flags


def test_mixed_series_refused():
    data = series()
    extra = deepcopy(data.records[0])
    extra.product = "reanalysis"
    data.records.append(extra)
    with pytest.raises(ValueError):
        weather_at_cutoff(data, T)


def test_multi_cell_batch_request_and_provenance(tmp_path):
    req = request(cells=[weather_cell(39.4, -121.4), weather_cell(40, -121)])
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json=payload(req))

    batch = client(tmp_path, handler).fetch(req)
    assert len(calls) == 1 and len(batch.series.records) == 50
    assert calls[0].url.params["wind_speed_unit"] == "ms"
    assert calls[0].url.params["timezone"] == "GMT"
    assert "," in calls[0].url.params["latitude"]
    assert Path(batch.asset.storage_path).is_file()
    assert batch.asset.request_metadata["grid_version"] == "grid-0p1-v1"


@pytest.mark.parametrize("status", [301, 400, 401, 403, 404])
def test_permanent_errors_not_retried(tmp_path, status):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(status, text="private-provider-body")

    with pytest.raises(WeatherError) as exc:
        client(tmp_path, handler).fetch(request())
    assert len(calls) == 1 and "private-provider-body" not in str(exc.value)


def test_bounded_transient_retries(tmp_path):
    calls = []
    req = request()

    def handler(r):
        calls.append(r)
        return httpx.Response(503) if len(calls) < 3 else httpx.Response(200, json=payload(req))

    assert len(client(tmp_path, handler).fetch(req).series.records) == 25
    assert len(calls) == 3


def test_timeout_exhaustion(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        raise httpx.ReadTimeout("private", request=r)

    with pytest.raises(WeatherError):
        client(tmp_path, handler).fetch(request())
    assert len(calls) == 3


@pytest.mark.parametrize(
    "body,headers", [("html", {}), ("bad", {"content-type": "application/json"})]
)
def test_non_json(tmp_path, body, headers):
    with pytest.raises(WeatherError):
        client(tmp_path, lambda r: httpx.Response(200, text=body, headers=headers)).fetch(request())


def test_rate_limit_shared_across_clients(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(429, headers={"Retry-After": "3600"})

    first = client(tmp_path, handler)
    with pytest.raises(WeatherError):
        first.fetch(request())
    with pytest.raises(WeatherError):
        client(tmp_path, handler).fetch(request())
    assert len(calls) == 1


def test_persistent_budget_and_expiry(tmp_path):
    path = tmp_path / "budget.sqlite"
    WeatherBudget(path).reserve(NOW, 30)
    with pytest.raises(WeatherError):
        WeatherBudget(path).reserve(NOW, 1)
    WeatherBudget(path).reserve(NOW + timedelta(seconds=61), 1)


@pytest.mark.parametrize(
    "cls,product,model",
    [
        (HistoricalForecastAdapter, "historical_forecast", "gfs_seamless"),
        (LiveWeatherAdapter, "live", "gfs_seamless"),
        (ReanalysisAdapter, "reanalysis", "era5"),
    ],
)
def test_separate_adapters(tmp_path, cls, product, model):
    req = request(start=NOW - 24 * HOUR, end=NOW, mode="live", product=product, model=model)
    result = client(tmp_path, lambda r: httpx.Response(200, json=payload(req)), cls=cls).fetch(req)
    assert result.series.records[0].product == product
    other = (
        request()
        if product != "historical_forecast"
        else request(product="reanalysis", model="era5")
    )
    with pytest.raises(ValueError):
        client(tmp_path, lambda _: None, cls=cls).fetch(other)


def test_cli_invalid_request_before_database():
    result = CliRunner().invoke(
        app,
        ["import-weather", "--latitude", "1", "--longitude", "2", "--start", "bad", "--end", "bad"],
    )
    assert result.exit_code == 1 and "Weather import failed" in result.output


def test_response_size_bound(tmp_path):
    with pytest.raises(WeatherError, match="size bound"):
        client(
            tmp_path,
            lambda r: httpx.Response(
                200,
                content=b" " * (8 * 1024 * 1024 + 1),
                headers={"content-type": "application/json"},
            ),
        ).fetch(request())


def test_nonzero_timestamp_offset_refused():
    req = request()
    data = payload(req)
    data["hourly"]["time"][0] = "2024-07-25T00:00+01:00"
    with pytest.raises(WeatherError):
        parse_weather(data, req, NOW, uuid4(), "v1")


def test_atomic_budget_reservations(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "concurrent.sqlite"

    def reserve(_):
        try:
            WeatherBudget(path).reserve(NOW, 10)
            return True
        except WeatherError:
            return False

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert sum(executor.map(reserve, range(8))) == 3


def test_future_and_old_live_requests_do_not_send_http(tmp_path):
    calls = []
    provider = client(tmp_path, lambda r: calls.append(r))
    for req in [
        request(start=NOW, end=NOW + HOUR, mode="live", product="live"),
        request(mode="live", product="live"),
    ]:
        with pytest.raises(ValueError):
            provider.fetch(req)
    assert calls == []


def test_exact_staleness_and_duplicate_hour_boundaries():
    data = series()
    end = data.records[-1].valid_at
    assert "weather_stale" not in weather_at_cutoff(data, end + 3 * HOUR).quality_flags
    assert (
        "weather_stale"
        in weather_at_cutoff(data, end + 3 * HOUR + timedelta(seconds=1)).quality_flags
    )
    data.records.append(deepcopy(data.records[0]))
    with pytest.raises(ValueError):
        weather_at_cutoff(data, end)


@pytest.mark.parametrize("key", ["time", "precipitation"])
def test_string_instead_of_array_refused(key):
    req = request()
    data = payload(req)
    data["hourly"][key] = "1" * len(req.hours())
    with pytest.raises(WeatherError):
        parse_weather(data, req, NOW, uuid4(), "v1")
