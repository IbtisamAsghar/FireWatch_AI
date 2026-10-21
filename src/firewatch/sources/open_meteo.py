"""Bounded Open-Meteo adapters, explicit UTC intervals and versioned grid cells."""

import hashlib
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import UUID, uuid4

import httpx
from pydantic import Field, model_validator

from firewatch.schemas.common import DTO, Latitude, Longitude, RunMode, UTCDateTime
from firewatch.schemas.contracts import SourceAssetInput, WeatherRecordInput, WeatherSeries
from firewatch.sources.weather_budget import WeatherBudget, WeatherError

WeatherProduct = Literal["live", "historical_forecast", "reanalysis"]
ENDPOINTS = {
    "live": "https://api.open-meteo.com/v1/forecast",
    "historical_forecast": "https://historical-forecast-api.open-meteo.com/v1/forecast",
    "reanalysis": "https://archive-api.open-meteo.com/v1/archive",
}
VARIABLES = {
    "temperature_2m": ("temperature_c", "°C"),
    "relative_humidity_2m": ("humidity_pct", "%"),
    "wind_speed_10m": ("wind_ms", "m/s"),
    "wind_gusts_10m": ("gust_ms", "m/s"),
    "precipitation": ("precipitation_mm", "mm"),
}
NORMALIZER = "weather-v1"
GRID = "grid-0p1-v1"
HOUR = timedelta(hours=1)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Weather requires aware timestamps")
    return value.astimezone(UTC)


class WeatherCell(DTO):
    key: str
    latitude: Latitude
    longitude: Longitude

    @model_validator(mode="after")
    def canonical(self) -> Self:
        expected = weather_cell(self.latitude, self.longitude, validate=False)
        if self.key != expected.key or (self.latitude, self.longitude) != (
            expected.latitude,
            expected.longitude,
        ):
            raise ValueError("Weather cell must use the canonical grid center")
        return self


def weather_cell(latitude: float, longitude: float, *, validate: bool = True) -> WeatherCell:
    """Half-open cells anchored at (-90,-180); poles/dateline clamp to the edge cell."""
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("Finite coordinates required")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Coordinates outside world bounds")
    # Decimal strings avoid binary rounding assigning an exact boundary to its west cell.
    from decimal import ROUND_FLOOR, Decimal

    y = min(1799, int(((Decimal(str(latitude)) + 90) * 10).to_integral_value(rounding=ROUND_FLOOR)))
    x = min(
        3599, int(((Decimal(str(longitude)) + 180) * 10).to_integral_value(rounding=ROUND_FLOOR))
    )
    values: dict[str, Any] = dict(
        key=f"{GRID}-{y}-{x}",
        latitude=round(-90 + (y + 0.5) / 10, 2),
        longitude=round(-180 + (x + 0.5) / 10, 2),
    )
    return WeatherCell(**values) if validate else WeatherCell.model_construct(**values)


class WeatherRequest(DTO):
    cells: list[WeatherCell] = Field(min_length=1, max_length=8)
    start: UTCDateTime
    end: UTCDateTime
    product: WeatherProduct = "historical_forecast"
    model: Literal["gfs_seamless", "era5"] = "gfs_seamless"
    source_version: str = Field(default=NORMALIZER, pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    mode: RunMode = RunMode.REPLAY

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.start > self.end or self.end - self.start > timedelta(days=7):
            raise ValueError("Request requires an inclusive interval of at most seven days")
        if any(t.minute or t.second or t.microsecond for t in (self.start, self.end)):
            raise ValueError("Weather request endpoints must be whole UTC hours")
        if len({c.key for c in self.cells}) != len(self.cells):
            raise ValueError("Request cells must be unique")
        if self.product == "live" and self.mode == RunMode.REPLAY:
            raise ValueError("Replay cannot request live weather")
        if (self.product == "reanalysis") != (self.model == "era5"):
            raise ValueError("Use GFS for forecast products and ERA5 for reanalysis")
        return self

    def hours(self) -> list[datetime]:
        return [self.start + i * HOUR for i in range(int((self.end - self.start) / HOUR) + 1)]


class WeatherBatch(DTO):
    asset_id: UUID
    asset: SourceAssetInput
    series: WeatherSeries


def parse_weather(
    payload: object,
    request: WeatherRequest,
    retrieved_at: datetime,
    asset_id: UUID,
    revision: str,
) -> WeatherSeries:
    """Reject schema/unit ambiguity; preserve missing/invalid values as null with flags."""
    responses = payload if isinstance(payload, list) else [payload]
    if len(responses) != len(request.cells):
        raise WeatherError("Weather response location count does not match request.")
    records = []
    try:
        for cell, data in zip(request.cells, responses, strict=True):
            if not isinstance(data, dict) or data.get("error"):
                raise ValueError("Invalid response")
            if data.get("utc_offset_seconds") != 0 or data.get("timezone") not in {
                "GMT",
                "UTC",
                "Etc/GMT",
                "Etc/UTC",
            }:
                raise ValueError("Response is not UTC")
            lat, lon = data["latitude"], data["longitude"]
            if isinstance(lat, bool) or isinstance(lon, bool):
                raise ValueError("Invalid coordinates")
            lat, lon = float(lat), float(lon)
            if (
                not math.isfinite(lat)
                or not math.isfinite(lon)
                or not (-90 <= lat <= 90 and -180 <= lon <= 180)
                or abs(lat - cell.latitude) > 1
                or abs(lon - cell.longitude) > 1
            ):
                raise ValueError("Returned grid point is outside requested neighborhood")
            hourly, units = data["hourly"], data["hourly_units"]
            if not isinstance(hourly, dict) or not isinstance(units, dict):
                raise ValueError("Hourly data must be objects")
            if units.get("time") != "iso8601":
                raise ValueError("Invalid time unit")
            if not isinstance(hourly, dict) or not isinstance(units, dict):
                raise ValueError("Hourly data must be objects")
            if not isinstance(hourly.get("time"), list):
                raise ValueError("Hourly times must be an array")
            times = []
            for value in hourly["time"]:
                if not isinstance(value, str):
                    raise ValueError("Invalid time")
                parsed = datetime.fromisoformat(value)
                # ISO8601 hourly values have no suffix when timezone=GMT.
                t = parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else utc(parsed)
                if parsed.tzinfo is not None and parsed.utcoffset() != timedelta(0):
                    raise ValueError("Hourly timestamps must agree with UTC metadata")
                if t.minute or t.second or t.microsecond:
                    raise ValueError("Invalid hourly boundary")
                times.append(t)
            if times != sorted(set(times)) or len(times) > 240:
                raise ValueError("Duplicate, unordered or oversized hourly response")
            for variable, (_, unit) in VARIABLES.items():
                if (
                    not isinstance(hourly[variable], list)
                    or units.get(variable) != unit
                    or len(hourly[variable]) != len(times)
                ):
                    raise ValueError("Unexpected unit or array size")
            indices = {t: i for i, t in enumerate(times)}
            for t in request.hours():
                flags = [
                    "precipitation_preceding_hour",
                    "hourly_model_values",
                    "provider_interpolation_unknown",
                ]
                if request.product != "live":
                    flags.append("retrospective_weather")
                if request.product == "reanalysis":
                    flags.append("fallback_product_reanalysis")
                values: dict[str, Any] = {}
                index = indices.get(t)
                if index is None:
                    flags.append("weather_hour_missing")
                for variable, (field, _) in VARIABLES.items():
                    value = hourly[variable][index] if index is not None else None
                    number = (
                        float(value)
                        if isinstance(value, (int, float)) and not isinstance(value, bool)
                        else None
                    )
                    valid = number is not None and math.isfinite(number)
                    if number is not None and valid:
                        valid = (
                            number >= -273.15
                            if field == "temperature_c"
                            else 0 <= number <= 100
                            if field == "humidity_pct"
                            else number >= 0
                        )
                    values[field] = number if valid else None
                    if not valid:
                        flags.append(f"{field}_missing" if value is None else f"{field}_invalid")
                records.append(
                    WeatherRecordInput(
                        cell_key=cell.key,
                        latitude=lat,
                        longitude=lon,
                        requested_latitude=cell.latitude,
                        requested_longitude=cell.longitude,
                        model=request.model,
                        product=request.product,
                        source_version=revision,
                        valid_at=t,
                        retrieved_at=retrieved_at,
                        asset_id=asset_id,
                        units={field: unit for field, unit in VARIABLES.values()},
                        quality_flags=flags,
                        **values,
                    )
                )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise WeatherError(
            "Weather response schema, coordinates, time or units are invalid."
        ) from None
    return WeatherSeries(records=records)


class OpenMeteoClient:
    def __init__(
        self,
        output_dir: Path,
        budget: WeatherBudget,
        *,
        client: httpx.Client | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.output_dir, self.budget = output_dir, budget
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False
        )
        self.owns_client = client is None
        self.now, self.sleep = now, sleep

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def fetch(self, request: WeatherRequest) -> WeatherBatch:
        request = WeatherRequest.model_validate(request.model_dump())
        now = utc(self.now())
        if request.end > now.replace(minute=0, second=0, microsecond=0):
            raise ValueError("Only completed weather hours may be requested")
        if request.product == "live" and request.start < now - timedelta(days=5):
            raise ValueError("Use historical forecast for old weather")
        params = {
            "latitude": ",".join(str(c.latitude) for c in request.cells),
            "longitude": ",".join(str(c.longitude) for c in request.cells),
            "start_date": request.start.date().isoformat(),
            "end_date": request.end.date().isoformat(),
            "hourly": ",".join(VARIABLES),
            "models": request.model,
            "timezone": "GMT",
            "timeformat": "iso8601",
            "temperature_unit": "celsius",
            "wind_speed_unit": "ms",
            "precipitation_unit": "mm",
        }
        # One request at a time; charge every location/calendar day conservatively.
        weight = len(request.cells) * ((request.end.date() - request.start.date()).days + 1)
        if weight > 30:
            raise ValueError("Split weather requests into at most 30 cell-calendar-days")
        raw = b""
        deadline = time.monotonic() + 90
        for attempt in range(3):
            if time.monotonic() > deadline:
                raise WeatherError("Weather operation deadline exceeded.")
            self.budget.reserve(utc(self.now()), weight)
            retry = False
            try:
                with self.client.stream(
                    "GET",
                    ENDPOINTS[request.product],
                    params=params,
                    timeout=httpx.Timeout(20, connect=5),
                    follow_redirects=False,
                ) as response:
                    if response.status_code == 429:
                        delay = 60.0
                        header = response.headers.get("Retry-After", "60")
                        try:
                            delay = float(header)
                        except ValueError:
                            try:
                                delay = (
                                    parsedate_to_datetime(header) - utc(self.now())
                                ).total_seconds()
                            except (TypeError, ValueError, OverflowError):
                                pass
                        self.budget.defer(utc(self.now()), delay)
                        raise WeatherError("Weather provider rate limited; retry after cooldown.")
                    if response.status_code in {408, 500, 502, 503, 504}:
                        retry = True
                    elif response.status_code != 200:
                        raise WeatherError("Weather provider rejected the request.")
                    else:
                        if "json" not in response.headers.get("content-type", "").lower():
                            raise WeatherError("Weather provider did not return JSON.")
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            if time.monotonic() > deadline:
                                raise WeatherError("Weather operation deadline exceeded.")
                            size += len(chunk)
                            if size > 8 * 1024 * 1024:
                                raise WeatherError("Weather response exceeds size bound.")
                            chunks.append(chunk)
                        raw = b"".join(chunks)
            except httpx.TransportError:
                retry = True
            if not retry:
                break
            if attempt == 2:
                raise WeatherError("Weather provider unavailable after bounded retries.") from None
            self.sleep(float(2**attempt))
        retrieved = utc(self.now())
        asset_id = uuid4()
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise WeatherError("Weather provider returned invalid JSON.") from None
        revision = f"{request.source_version}-{asset_id.hex}"
        series = parse_weather(payload, request, retrieved, asset_id, revision)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"{asset_id}.json"
        # Exclusive file creation: validated evidence is never overwritten.
        with target.open("xb") as output:
            output.write(raw)
        asset = SourceAssetInput(
            provider="open_meteo",
            product=request.product,
            retrieved_at=retrieved,
            storage_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            format="json",
            processing_version=NORMALIZER,
            request_metadata={
                "endpoint": ENDPOINTS[request.product],
                "attribution": "https://open-meteo.com/",
                "license": "CC-BY-4.0",
                "grid_version": GRID,
                "cache_namespace": request.source_version,
                "request": request.model_dump(mode="json"),
                "precipitation_interval": "(valid_at-1h,valid_at]",
                "interpolation": "provider_hourly_series; native cadence not asserted",
            },
        )
        return WeatherBatch(asset_id=asset_id, asset=asset, series=series)


class LiveWeatherAdapter(OpenMeteoClient):
    def fetch(self, request: WeatherRequest) -> WeatherBatch:
        if request.product != "live":
            raise ValueError("Live adapter only accepts live requests")
        return super().fetch(request)


class HistoricalForecastAdapter(OpenMeteoClient):
    def fetch(self, request: WeatherRequest) -> WeatherBatch:
        if request.product != "historical_forecast":
            raise ValueError("Historical adapter only accepts historical forecast requests")
        return super().fetch(request)


class ReanalysisAdapter(OpenMeteoClient):
    def fetch(self, request: WeatherRequest) -> WeatherBatch:
        if request.product != "reanalysis":
            raise ValueError("Reanalysis adapter requires explicit reanalysis opt-in")
        return super().fetch(request)
