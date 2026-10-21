"""Source-access acceptance uses invented CSV/HTTP fixtures, never real fire labels."""

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.config import Settings
from firewatch.repositories.regions import load_regions
from firewatch.schemas.common import Product
from firewatch.sources.access import (
    FIRMS,
    WEATHER_UNITS,
    AccessError,
    archive_inspection,
    inspect_sample,
    inspect_weather,
    new_output,
    parse_availability,
    probe_sources,
    read_response,
)

ROOT = Path(__file__).resolve().parents[2]
KEY = "synthetic-test-map-key-1234567890"
HEADER = (
    "latitude,longitude,acq_date,acq_time,satellite,confidence,frp,bright_ti4,scan,track,version\n"
)
ROW = "39,-121,2022-09-07,0037,N20,n,10.2,320.1,0.4,0.5,2.0NRT\n"
AVAILABILITY = (
    b"data_id,min_date,max_date\n"
    b"VIIRS_NOAA20_NRT,2026-10-01,2026-10-20\n"
    b"VIIRS_NOAA20_SP,2018-04-01,2026-05-31\n"
    b"VIIRS_NOAA21_NRT,2026-10-01,2026-10-20\n"
)


def region():
    return next(
        item
        for item in load_regions(ROOT / "configs/regions.yaml").regions
        if item.name == "northern-california"
    )


def weather(start, days=3, latitude=39.5, longitude=-121.25):
    return {
        "latitude": latitude,
        "longitude": longitude,
        "utc_offset_seconds": 0,
        "hourly_units": WEATHER_UNITS.copy(),
        "hourly": {
            "time": [
                (datetime.combine(start, datetime.min.time()) + timedelta(hours=i)).isoformat(
                    timespec="minutes"
                )
                for i in range(days * 24)
            ],
            **{name: [10] * (days * 24) for name in WEATHER_UNITS},
        },
    }


def sample(content=None, **kwargs):
    return inspect_sample(
        (HEADER + ROW).encode() if content is None else content,
        kwargs.get("product", Product.NOAA20_SP),
        region().bbox,
        date(2022, 9, 7),
        3,
    )


def test_availability_retains_both_sensors_and_separate_products():
    rows = parse_availability(AVAILABILITY)
    assert len(rows) == 3
    assert rows[0].covers(date(2026, 10, 20), 1)
    assert not rows[0].covers(date(2026, 10, 20), 2)
    assert rows[1].product == Product.NOAA20_SP


@pytest.mark.parametrize(
    "content",
    [
        b"<html>Invalid key</html>",
        b"data_id,min_date,max_date\nVIIRS_NOAA20_SP,2025-01-01,2024-01-01\n",
        b"data_id,min_date,max_date\nVIIRS_NOAA20_SP,not-a-date,2024-01-01\n",
        b"data_id,min_date,max_date\nMODIS_SP,2020-01-01,2024-01-01\n",
        AVAILABILITY + b"VIIRS_NOAA20_SP,2018-04-01,2026-05-31\n",
    ],
)
def test_availability_rejects_provider_errors_and_ambiguous_dates(content):
    with pytest.raises(AccessError):
        parse_availability(content)


def test_csv_types_leading_zero_time_units_and_raw_categories_are_preserved():
    result = sample()
    assert result.first_acquisition.isoformat() == "2022-09-07T00:37:00+00:00"
    assert result.observed_confidence == ["n"]
    assert result.observed_satellites == ["N20"]
    assert result.column_types["acq_time"] == ["integer_text"]
    assert result.column_types["frp"] == ["number_text"]
    assert result.observed_months == ["2022-09"]
    assert result.requested_end == date(2022, 9, 9)
    assert result.sha256 == hashlib.sha256((HEADER + ROW).encode()).hexdigest()
    assert sample((HEADER + ROW.replace(",10.2,", ",,")).encode()).missing_values["frp"] == 1


def test_header_only_is_a_valid_empty_sample_but_not_a_coverage_claim():
    result = sample(HEADER.encode())
    assert result.empty and result.row_count == 0
    assert result.first_acquisition is None and result.observed_months == []


@pytest.mark.parametrize(
    "old,new",
    [
        ("39,-121", "99,-121"),
        ("39,-121", "39,71"),
        ("2022-09-07", "2024-07-25"),
        ("0037", "2460"),
        ("0037", "0.37"),
        ("N20", "N21"),
        ("10.2", "-1"),
        ("10.2", "NaN"),
        ("320.1", "-999"),
    ],
)
def test_sample_rejects_bad_coordinates_time_sensor_and_measurements(old, new):
    with pytest.raises(AccessError):
        sample((HEADER + ROW.replace(old, new)).encode())


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"<html>Rate limit</html>",
        b"Invalid MAP_KEY",
        (HEADER + ROW.rstrip() + ",extra\n").encode(),
        (HEADER.replace("frp,", "latitude,") + ROW).encode(),
        b"\xff\xfe",
    ],
)
def test_sample_rejects_malformed_csv(content):
    with pytest.raises(AccessError):
        sample(content)


@pytest.mark.parametrize("status", [301, 403, 429, 500])
def test_http_errors_never_echo_key_url_or_provider_body(status):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, text=KEY))
    with httpx.Client(transport=transport) as client, pytest.raises(AccessError) as error:
        read_response(client, f"{FIRMS}/api/area/csv/{KEY}/x", SecretStr(KEY))
    assert KEY not in str(error.value)
    assert "https" not in str(error.value)


def test_echoed_credentials_are_discarded_and_network_exceptions_are_safe():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=KEY))
    ) as client:
        with pytest.raises(AccessError, match="discarded"):
            read_response(client, FIRMS, SecretStr(KEY))

    def timeout(request):
        raise httpx.ReadTimeout(f"secret URL {KEY}", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(AccessError) as error:
            read_response(client, FIRMS)
        assert KEY not in str(error.value)


def test_oversized_response_is_stopped(monkeypatch):
    monkeypatch.setattr("firewatch.sources.access.MAX_BYTES", 4)
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"12345"))
    ) as client:
        with pytest.raises(AccessError, match="limit"):
            read_response(client, FIRMS)


def test_weather_requires_hourly_shape_units_and_exposes_missing_values():
    data = weather(date(2022, 9, 7))
    result = inspect_weather(json.dumps(data).encode(), 39.5, -121.25, date(2022, 9, 7), 3)
    assert result.complete and result.expected_hours == 72
    data["hourly"]["temperature_2m"][0] = None
    result = inspect_weather(json.dumps(data).encode(), 39.5, -121.25, date(2022, 9, 7), 3)
    assert not result.complete and result.valid_hours["temperature_2m"] == 71


@pytest.mark.parametrize(
    "change", ["units", "time", "offset", "coordinate", "negative", "humidity"]
)
def test_weather_rejects_wrong_units_times_grid_cells_and_sentinels(change):
    data = weather(date(2022, 9, 7))
    if change == "units":
        data["hourly_units"]["wind_speed_10m"] = "km/h"
    elif change == "time":
        data["hourly"]["time"][0] = "2026-10-21T00:00"
    elif change == "offset":
        data["utc_offset_seconds"] = 3600
    elif change == "coordinate":
        data["latitude"] = 34
    elif change == "negative":
        data["hourly"]["wind_speed_10m"][0] = -999
    else:
        data["hourly"]["relative_humidity_2m"][0] = 101
    with pytest.raises(AccessError):
        inspect_weather(json.dumps(data).encode(), 39.5, -121.25, date(2022, 9, 7), 3)


def source_transport(request):
    if "data_availability" in request.url.path:
        return httpx.Response(200, content=AVAILABILITY)
    if "area/csv" in request.url.path:
        day = request.url.path.split("/")[-1]
        return httpx.Response(200, content=(HEADER + ROW.replace("2022-09-07", day)).encode())
    params = request.url.params
    assert request.url.host == "historical-forecast-api.open-meteo.com"
    assert params["models"] == "gfs_seamless" and params["timezone"] == "GMT"
    start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
    return httpx.Response(
        200,
        json=weather(
            start, (end - start).days + 1, float(params["latitude"]), float(params["longitude"])
        ),
    )


def test_probe_produces_hashed_raw_assets_and_safe_manifests_without_enabling_region(tmp_path):
    selected = region()
    original_configuration = selected.model_dump()
    with httpx.Client(transport=httpx.MockTransport(source_transport)) as client:
        report = probe_sources(selected, tmp_path, SecretStr(KEY), client)
    assert report.status == "probed"
    assert len(report.samples) == 3 and len(report.weather) == 10
    assert Product.NOAA21_SP in report.unavailable_products
    assert "2024-07" in report.advertised_months[Product.NOAA20_SP]
    assert report.advertised_months[Product.NOAA20_NRT] == []
    assert not selected.enabled and selected.model_dump() == original_configuration
    for name, digest in report.files_sha256.items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest
    saved = (tmp_path / "report.json").read_text()
    assert KEY not in saved and "{MAP_KEY}" in saved
    assert report.request_manifests["historical-1"]["start_date"] == "2022-09-07"


def test_missing_key_saves_blocked_report_and_never_calls_provider(tmp_path):
    def unexpected(request):
        pytest.fail("No network request should occur without a key")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(AccessError, match="missing"):
            probe_sources(region(), tmp_path, None, client)
    assert json.loads((tmp_path / "report.json").read_text())["status"] == "blocked"


def test_weather_only_is_explicitly_incomplete_firms_acceptance(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(source_transport)) as client:
        report = probe_sources(region(), tmp_path, None, client, weather_only=True)
    assert report.status == "blocked" and len(report.weather) == 10
    assert report.samples == {} and report.availability == []


def test_partial_failure_preserves_raw_evidence_and_blocked_report(tmp_path):
    def invalid(request):
        if "area/csv" in request.url.path:
            return httpx.Response(200, text="<html>temporarily unavailable</html>")
        return source_transport(request)

    with httpx.Client(transport=httpx.MockTransport(invalid)) as client:
        with pytest.raises(AccessError):
            probe_sources(region(), tmp_path, SecretStr(KEY), client)
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["status"] == "blocked" and "recent.csv" in report["files_sha256"]


def test_local_archive_inspection_keeps_original_bytes_and_needs_no_database(tmp_path):
    path = tmp_path / "trusted.csv"
    content = (HEADER + ROW).encode()
    path.write_bytes(content)
    result = archive_inspection(path, Product.NOAA20_SP, region(), date(2022, 9, 7), 3)
    assert not result["imported"] and result["inspection"]["row_count"] == 1
    assert path.read_bytes() == content
    assert new_output(tmp_path) != new_output(tmp_path)


def test_region_study_rejects_mixed_primary_sensors_and_unverified_status():
    plan = region().source_access.model_dump() | {"status": "candidate", "evidence_report": None}
    for update in [
        {"historical_product": Product.NOAA21_SP},
        {"status": "reviewed"},
        {"season_months": [0]},
        {"requested_months": ["2024-13"]},
    ]:
        with pytest.raises(ValidationError):
            type(region().source_access).model_validate(plan | update)


def test_cli_missing_key_has_safe_error_and_inspection_requires_no_secrets(
    tmp_path, monkeypatch, caplog
):
    settings = Settings(
        database_url="postgresql+psycopg://test:password@localhost/test",
        admin_api_token="synthetic-test-operator-" + "x" * 40,
        firms_map_key=None,
        _env_file=None,
    )
    monkeypatch.setattr("firewatch.cli.get_settings", lambda: settings)
    monkeypatch.chdir(ROOT)
    result = CliRunner().invoke(app, ["probe-sources", "--output", str(tmp_path)])
    assert result.exit_code == 1 and "FIRMS_MAP_KEY is missing" in result.output
    path = tmp_path / "trusted.csv"
    path.write_text(HEADER + ROW)
    result = CliRunner().invoke(
        app,
        [
            "inspect-firms-sample",
            "--path",
            str(path),
            "--start",
            "2022-09-07",
            "--days",
            "3",
            "--product",
            "VIIRS_NOAA20_SP",
        ],
    )
    assert result.exit_code == 0 and json.loads(result.output)["imported"] is False


def test_cli_installs_redaction_for_http_library_debug_logs(tmp_path, monkeypatch, caplog):
    settings = Settings(
        database_url="postgresql+psycopg://test:password@localhost/test",
        admin_api_token="synthetic-test-operator-" + "x" * 40,
        firms_map_key=KEY,
        _env_file=None,
    )
    monkeypatch.setattr("firewatch.cli.get_settings", lambda: settings)

    def fail(*args):
        logging.getLogger("httpx").warning("GET %s/api/area/csv/%s/product", FIRMS, KEY)
        raise AccessError("Fixed safe failure")

    monkeypatch.setattr("firewatch.sources.access.probe_sources", fail)
    monkeypatch.chdir(ROOT)
    with caplog.at_level(logging.WARNING):
        result = CliRunner().invoke(app, ["probe-sources", "--output", str(tmp_path)])
    assert result.exit_code == 1
    assert KEY not in result.output and KEY not in caplog.text
