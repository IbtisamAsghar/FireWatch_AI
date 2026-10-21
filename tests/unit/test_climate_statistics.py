"""Reference statistics, missingness and future-exclusion regression tests."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from power_factory import NOW, batches, payload, request
from typer.testing import CliRunner
from weather_factory import series

from firewatch.cli import app
from firewatch.processing.baselines import (
    BaselineSpec,
    baseline_manifest,
    climate_context,
    compute_baselines,
    load_baseline_spec,
)
from firewatch.schemas.common import Provider
from firewatch.sources.power import PowerClient, climate_cell, parse_power
from firewatch.sources.weather_budget import WeatherBudget, WeatherError


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    return batches(tmp_path_factory.mktemp("power-unit"))


@pytest.mark.parametrize(
    "lat,lon,expected",
    [
        (0, 0, (0.25, 0.3125)),
        (-90, -180, (-89.75, -179.6875)),
        (90, 180, (89.75, 179.6875)),
        (39.4, -121.4, (39.25, -121.5625)),
    ],
)
def test_coarse_grid(lat, lon, expected):
    cell = climate_cell(lat, lon)
    assert (cell.latitude, cell.longitude) == expected
    assert climate_cell(*expected) == cell


@pytest.mark.parametrize("lat,lon", [(91, 0), (0, 181), (float("nan"), 0), (0, float("inf"))])
def test_invalid_cell(lat, lon):
    with pytest.raises(ValueError):
        climate_cell(lat, lon)


@pytest.mark.parametrize(
    "changes",
    [
        dict(reference_start_year=2015),
        dict(reference_start_year=1980),
        dict(reference_end_year=2022),
        dict(earliest_snapshot="2019-12-31T12:00:00Z"),
        dict(earliest_snapshot="2022-01-01"),
        dict(min_valid_fraction=0),
        dict(min_valid_fraction=1.1),
        dict(version="../bad"),
    ],
)
def test_reference_bounds(changes):
    with pytest.raises(ValueError):
        BaselineSpec(**changes)


def test_configuration_is_fixed():
    spec = load_baseline_spec(Path("configs/features-v1.yaml"))
    assert (spec.reference_start_year, spec.reference_end_year) == (2010, 2019)
    assert spec.earliest_snapshot == datetime(2022, 5, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    "damage",
    [
        "time",
        "fill",
        "temperature_unit",
        "rain_unit",
        "coordinate",
        "dates",
        "extra_date",
        "array",
        "error",
        "geometry",
    ],
)
def test_metadata_must_be_valid(damage):
    req = request()
    data = payload(req)
    if damage == "time":
        data["header"]["time_standard"] = "LST"
    if damage == "fill":
        del data["header"]["fill_value"]
    if damage == "temperature_unit":
        data["parameters"]["T2M"]["units"] = "K"
    if damage == "rain_unit":
        data["parameters"]["PRECTOTCORR"]["units"] = "mm/hour"
    if damage == "coordinate":
        data["geometry"]["coordinates"][0] = 0
    if damage == "dates":
        data["header"]["end"] = "20100103"
    if damage == "extra_date":
        data["properties"]["parameter"]["T2M"]["20220101"] = 10
    if damage == "array":
        data["properties"]["parameter"]["T2M"] = []
    if damage == "error":
        data["messages"] = ["provider error secret"]
    if damage == "geometry":
        data["geometry"]["type"] = "Polygon"
    with pytest.raises(WeatherError):
        parse_power(data, req)


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("T2M", -999),
        ("T2M", None),
        ("T2M", -300),
        ("T2M", float("nan")),
        ("PRECTOTCORR", -999),
        ("PRECTOTCORR", -1),
        ("PRECTOTCORR", True),
    ],
)
def test_fill_and_invalid_values_are_null(parameter, value):
    req = request()
    data = payload(req)
    data["properties"]["parameter"][parameter]["20100101"] = value
    row = parse_power(data, req)[0]
    assert getattr(row, "temperature_c" if parameter == "T2M" else "precipitation_mm") is None
    assert row.quality_flags


def test_provider_declared_fill_and_missing_date():
    req = request()
    data = payload(req)
    data["header"]["fill_value"] = -888
    data["properties"]["parameter"]["T2M"]["20100101"] = -888
    del data["properties"]["parameter"]["PRECTOTCORR"]["20100102"]
    rows = parse_power(data, req)
    assert len(rows) == 365 and rows[0].temperature_c is None
    assert rows[1].precipitation_mm is None
    assert rows[2].precipitation_mm == 2


def test_monthly_pooled_daily_means_leap_days_and_hash(reference):
    rows = compute_baselines(reference, BaselineSpec(), UUID(int=1))
    assert len(rows) == 12
    assert rows[1].sample_counts["expected_days"] == 282
    assert rows[0].sample_counts["temperature_c"] == 310
    assert all(r.temperature_mean_c == 10 and r.precipitation_mean_mm == 2 for r in rows)
    assert baseline_manifest(reference, BaselineSpec()) == baseline_manifest(
        list(reversed(reference)), BaselineSpec()
    )


def test_coverage_is_per_variable_and_no_fill_bias(reference):
    data = deepcopy(reference)
    for batch in data:
        for row in batch.days:
            if row.day.month == 1:
                row.temperature_c = None
    result = compute_baselines(data, BaselineSpec(), UUID(int=1))[0]
    assert result.temperature_mean_c is None and result.precipitation_mean_mm == 2
    assert result.sample_counts["temperature_c"] == 0
    assert "temperature_c_baseline_missing" in result.statistics["quality_flags"]


def test_insufficient_years_duplicates_and_partial_days(reference):
    for data in [reference[:-1], reference[:-1] + [reference[0]]]:
        with pytest.raises(ValueError):
            compute_baselines(data, BaselineSpec(), UUID(int=1))
    data = deepcopy(reference)
    data[0].days.pop()
    with pytest.raises(ValueError):
        compute_baselines(data, BaselineSpec(), UUID(int=1))


def test_departure_deficit_and_future_weather_exclusion(reference):
    weather = series()
    T = weather.records[-1].valid_at
    for row in weather.records:
        row.precipitation_mm = 0
    future = deepcopy(weather.records[-1])
    future.valid_at += timedelta(hours=1)
    future.temperature_c = 100
    weather.records.append(future)
    baseline = compute_baselines(reference, BaselineSpec(), UUID(int=1))[6]
    result = climate_context(weather, T, request().cell, baseline)
    assert result.temperature_departure_c == -9
    assert result.rainfall_deficit_24h_mm == 2
    assert "not hourly z-score" in result.temperature_definition
    for row in weather.records:
        row.precipitation_mm = 1
    assert climate_context(weather, T, request().cell, baseline).rainfall_deficit_24h_mm == 0


def test_missing_baseline_and_incomplete_rainfall(reference):
    weather = series()
    T = weather.records[-1].valid_at
    absent = climate_context(weather, T, request().cell, None)
    assert absent.temperature_departure_c is None and absent.rainfall_deficit_24h_mm is None
    assert "climate_baseline_missing" in absent.quality_flags
    baseline = compute_baselines(reference, BaselineSpec(), UUID(int=1))[6]
    weather.records[-3].precipitation_mm = None
    incomplete = climate_context(weather, T, request().cell, baseline)
    assert incomplete.temperature_departure_c == -9
    assert incomplete.rainfall_deficit_24h_mm is None
    assert "precipitation_24h_incomplete" in incomplete.quality_flags


@pytest.mark.parametrize("change", ["month", "cell", "future_reference", "utc", "source"])
def test_context_refuses_mismatched_baseline(reference, change):
    weather = series()
    T = weather.records[-1].valid_at
    baseline = compute_baselines(reference, BaselineSpec(), UUID(int=1))[6]
    if change == "month":
        baseline.month = 1
    if change == "cell":
        baseline.cell_key = "other"
    if change == "future_reference":
        baseline.reference_end_year = 2024
    if change == "utc":
        baseline.statistics["time_standard"] = "LST"
    if change == "source":
        baseline.source = Provider.OPEN_METEO
    with pytest.raises(ValueError):
        climate_context(weather, T, request().cell, baseline)


@pytest.mark.parametrize("status", [301, 400, 403, 404])
def test_http_permanent_failure_is_safe(tmp_path, status):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(status, text="secret-provider-body")

    provider = PowerClient(
        tmp_path / "raw",
        WeatherBudget(tmp_path / "budget"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )
    with pytest.raises(WeatherError) as exc:
        provider.fetch(request())
    assert len(calls) == 1 and "secret-provider-body" not in str(exc.value)


def test_cli_missing_coordinates_fails_before_fetch():
    result = CliRunner().invoke(app, ["build-baselines"])
    assert result.exit_code == 1 and "Climate baseline build failed" in result.output


def test_pooled_daily_weighting_and_exact_coverage_threshold(reference):
    data = deepcopy(reference)
    for batch in data:
        for row in batch.days:
            if row.day.month == 2:
                row.temperature_c = 100 if row.day.year == 2012 else 0
    feb = compute_baselines(data, BaselineSpec(), UUID(int=1))[1]
    assert feb.temperature_mean_c == pytest.approx(2900 / 282)
    data = deepcopy(reference)
    for row in data[0].days:
        if row.day.month == 1:
            row.temperature_c = None
    assert compute_baselines(data, BaselineSpec(), UUID(int=1))[0].temperature_mean_c == 10
    data[1].days[0].temperature_c = None
    assert compute_baselines(data, BaselineSpec(), UUID(int=1))[0].temperature_mean_c is None


def test_no_weather_values_are_invented(reference):
    weather = series()
    T = weather.records[-1].valid_at
    weather.records = []
    baseline = compute_baselines(reference, BaselineSpec(), UUID(int=1))[6]
    context = climate_context(weather, T, request().cell, baseline)
    assert context.temperature_departure_c is None and context.rainfall_deficit_24h_mm is None
    assert "weather_missing" in context.quality_flags


def test_power_retries_timeouts_rate_limit_and_utc_query(tmp_path):
    from power_factory import client

    provider, calls = client(tmp_path)
    req = request()
    provider.fetch(req)
    assert calls[0].url.params["time-standard"] == "UTC"
    assert calls[0].url.params["parameters"] == "T2M,PRECTOTCORR"
    attempts = []

    def handler(r):
        attempts.append(r)
        return httpx.Response(503) if len(attempts) < 3 else httpx.Response(200, json=payload(req))

    provider.client = httpx.Client(transport=httpx.MockTransport(handler))
    provider.fetch(req)
    assert len(attempts) == 3
    attempts.clear()

    def timeout(r):
        attempts.append(r)
        raise httpx.ReadTimeout("private", request=r)

    provider.client = httpx.Client(transport=httpx.MockTransport(timeout))
    with pytest.raises(WeatherError):
        provider.fetch(req)
    assert len(attempts) == 3
    provider.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(429, headers={"Retry-After": "3600"})
        )
    )
    with pytest.raises(WeatherError):
        provider.fetch(req)
    other, _ = client(tmp_path)
    with pytest.raises(WeatherError, match="cooldown"):
        other.fetch(req)


@pytest.mark.parametrize("case", ["html", "json", "oversize"])
def test_bad_power_http_payload(tmp_path, case):
    content = b"bad" if case != "oversize" else b" " * (8 * 1024 * 1024 + 1)
    content_type = "text/html" if case == "html" else "application/json"
    provider = PowerClient(
        tmp_path / "raw",
        WeatherBudget(tmp_path / "budget"),
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, content=content, headers={"content-type": content_type}
                )
            )
        ),
        now=lambda: NOW,
    )
    with pytest.raises(WeatherError):
        provider.fetch(request())


def test_future_reference_year_does_not_fetch(tmp_path):
    from power_factory import client

    provider, calls = client(tmp_path)
    with pytest.raises(ValueError):
        provider.fetch(request(2026))
    assert calls == []
