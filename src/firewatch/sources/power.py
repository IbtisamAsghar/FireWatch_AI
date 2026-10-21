"""NASA POWER UTC daily T2M/PRECTOTCORR adapter with bounded full-year requests."""

import hashlib
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Self
from uuid import UUID, uuid4

import httpx
from pydantic import Field, model_validator

from firewatch.schemas.common import DTO, Latitude, Longitude, Nonnegative
from firewatch.schemas.contracts import SourceAssetInput
from firewatch.sources.open_meteo import utc
from firewatch.sources.weather_budget import WeatherBudget, WeatherError

ENDPOINT = "https://power.larc.nasa.gov/api/temporal/daily/point"
GRID = "power-grid-0p5x0p625-v1"
VERSION = "power-daily-v1"


class ClimateCell(DTO):
    key: str
    latitude: Latitude
    longitude: Longitude

    @model_validator(mode="after")
    def canonical(self) -> Self:
        expected = climate_cell(self.latitude, self.longitude, validate=False)
        if self != expected:
            raise ValueError("Use a canonical climate cell center")
        return self


def climate_cell(latitude: float, longitude: float, *, validate: bool = True) -> ClimateCell:
    """Application coarse bins, not a claim that the API returns native grid coordinates."""
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        raise ValueError("Invalid climate coordinates")
    y = min(
        359,
        int(
            ((Decimal(str(latitude)) + 90) / Decimal(".5")).to_integral_value(rounding=ROUND_FLOOR)
        ),
    )
    x = min(
        575,
        int(
            ((Decimal(str(longitude)) + 180) / Decimal(".625")).to_integral_value(
                rounding=ROUND_FLOOR
            )
        ),
    )
    values: dict[str, Any] = dict(
        key=f"{GRID}-{y}-{x}", latitude=-90 + (y + 0.5) * 0.5, longitude=-180 + (x + 0.5) * 0.625
    )
    return ClimateCell(**values) if validate else ClimateCell.model_construct(**values)


class PowerRequest(DTO):
    cell: ClimateCell
    year: int = Field(ge=1981, le=2100)

    def days(self) -> list[date]:
        start, end = date(self.year, 1, 1), date(self.year + 1, 1, 1)
        return [start + timedelta(days=i) for i in range((end - start).days)]


class PowerDay(DTO):
    day: date
    temperature_c: float | None
    precipitation_mm: Nonnegative | None
    quality_flags: list[str] = Field(default_factory=list)


class PowerBatch(DTO):
    asset_id: UUID
    asset: SourceAssetInput
    request: PowerRequest
    days: list[PowerDay]


def parse_power(payload: object, request: PowerRequest) -> list[PowerDay]:
    """Validate declared UTC/units/fill before arithmetic; invalid daily values stay null."""
    try:
        if not isinstance(payload, dict) or payload.get("messages"):
            raise ValueError("Provider errors")
        header = payload["header"]
        if header["time_standard"] != "UTC":
            raise ValueError("UTC required")
        if header["start"] != f"{request.year}0101" or header["end"] != f"{request.year}1231":
            raise ValueError("Full year required")
        fill = header["fill_value"]
        if isinstance(fill, bool) or not isinstance(fill, (int, float)) or not math.isfinite(fill):
            raise ValueError("Missing fill metadata")
        if payload["parameters"]["T2M"]["units"] != "C":
            raise ValueError("Temperature unit")
        if payload["parameters"]["PRECTOTCORR"]["units"] != "mm/day":
            raise ValueError("Rain unit")
        coordinates = payload["geometry"]["coordinates"]
        if (
            payload["geometry"]["type"] != "Point"
            or len(coordinates) < 2
            or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in coordinates[:2]
            )
            or abs(coordinates[0] - request.cell.longitude) > 0.001
            or abs(coordinates[1] - request.cell.latitude) > 0.001
        ):
            raise ValueError("Coordinate mismatch")
        parameters = payload["properties"]["parameter"]
        wanted = {d.strftime("%Y%m%d") for d in request.days()}
        for key in ("T2M", "PRECTOTCORR"):
            if not isinstance(parameters[key], dict) or not set(parameters[key]) <= wanted:
                raise ValueError("Unexpected daily keys")
        rows = []
        for day in request.days():
            values: dict[str, Any] = {}
            flags = []
            for parameter, field in (("T2M", "temperature_c"), ("PRECTOTCORR", "precipitation_mm")):
                raw = parameters[parameter].get(day.strftime("%Y%m%d"))
                valid = (
                    isinstance(raw, (float, int))
                    and not isinstance(raw, bool)
                    and math.isfinite(raw)
                    and raw != fill
                )
                if valid:
                    valid = raw >= (-273.15 if parameter == "T2M" else 0)
                values[field] = float(raw) if valid else None
                if not valid:
                    flags.append(
                        f"{parameter}_missing"
                        if raw is None or raw == fill
                        else f"{parameter}_invalid"
                    )
            rows.append(PowerDay(day=day, quality_flags=flags, **values))
        return rows
    except (KeyError, TypeError, ValueError, OverflowError):
        raise WeatherError("POWER response units, UTC, coverage or metadata are invalid.") from None


def batch_from_raw(asset_id: UUID, asset: SourceAssetInput) -> PowerBatch:
    """Revalidate evidence whenever it crosses the persistence or computation boundary."""
    if (
        asset.provider != "nasa_power"
        or asset.product != VERSION
        or asset.processing_version != VERSION
        or asset.request_metadata.get("grid_version") != GRID
    ):
        raise WeatherError("POWER source metadata mismatch.")
    request = PowerRequest.model_validate(asset.request_metadata["request"])
    with Path(asset.storage_path).open("rb") as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != asset.content_hash:
        raise WeatherError("POWER raw evidence checksum mismatch.")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError):
        raise WeatherError("POWER evidence is not valid JSON.") from None
    return PowerBatch(
        asset_id=asset_id, asset=asset, request=request, days=parse_power(payload, request)
    )


class PowerClient:
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
            timeout=httpx.Timeout(30, connect=5), follow_redirects=False, trust_env=False
        )
        self.owns_client = client is None
        self.now, self.sleep = now, sleep

    def close(self) -> None:
        if self.owns_client:
            self.client.close()

    def fetch(self, request: PowerRequest) -> PowerBatch:
        request = PowerRequest.model_validate(request.model_dump())
        if datetime(request.year + 1, 1, 1, tzinfo=UTC) > utc(self.now()):
            raise ValueError("POWER reference years must be complete")
        params = dict(
            parameters="T2M,PRECTOTCORR",
            community="AG",
            longitude=str(request.cell.longitude),
            latitude=str(request.cell.latitude),
            start=f"{request.year}0101",
            end=f"{request.year}1231",
            format="JSON",
            **{"time-standard": "UTC"},
        )
        deadline = time.monotonic() + 120
        raw = b""
        for attempt in range(3):
            if time.monotonic() > deadline:
                raise WeatherError("POWER request deadline exceeded.")
            self.budget.reserve(utc(self.now()), 1)
            retry = False
            try:
                with self.client.stream(
                    "GET",
                    ENDPOINT,
                    params=params,
                    timeout=httpx.Timeout(30, connect=5),
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
                            except (ValueError, TypeError, OverflowError):
                                pass
                        self.budget.defer(utc(self.now()), delay)
                        raise WeatherError("POWER rate limited; retry after shared cooldown.")
                    if response.status_code in {408, 500, 502, 503, 504}:
                        retry = True
                    elif response.status_code != 200:
                        raise WeatherError("POWER rejected the request.")
                    else:
                        if "json" not in response.headers.get("content-type", "").lower():
                            raise WeatherError("POWER did not return JSON.")
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            if time.monotonic() > deadline:
                                raise WeatherError("POWER request deadline exceeded.")
                            size += len(chunk)
                            if size > 8 * 1024 * 1024:
                                raise WeatherError("POWER response size bound exceeded.")
                            chunks.append(chunk)
                        raw = b"".join(chunks)
            except httpx.TransportError:
                retry = True
            if not retry:
                break
            if attempt == 2:
                raise WeatherError("POWER unavailable after bounded retries.") from None
            self.sleep(float(2**attempt))
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise WeatherError("POWER did not return valid JSON.") from None
        days = parse_power(payload, request)
        asset_id = uuid4()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"{asset_id}.json"
        with target.open("xb") as output:
            output.write(raw)
        asset = SourceAssetInput(
            provider="nasa_power",
            product=VERSION,
            retrieved_at=utc(self.now()),
            storage_path=str(target.resolve()),
            content_hash=hashlib.sha256(raw).hexdigest(),
            format="json",
            processing_version=VERSION,
            request_metadata={
                "endpoint": ENDPOINT,
                "request": request.model_dump(mode="json"),
                "grid_version": GRID,
                "time_standard": "UTC",
                "attribution": "https://power.larc.nasa.gov/",
                "coarse_resolution_degrees": [0.5, 0.625],
            },
        )
        return PowerBatch(asset_id=asset_id, asset=asset, request=request, days=days)
