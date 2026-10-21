"""Bounded, read-only source reconnaissance; no ingestion or database publication."""

import csv
import hashlib
import io
import json
import math
import re
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr, model_validator

from firewatch.schemas.common import DTO, Product, UTCDateTime
from firewatch.schemas.contracts import BoundingBox, RegionDefinition

FIRMS = "https://firms.modaps.eosdis.nasa.gov"
WEATHER = "https://historical-forecast-api.open-meteo.com/v1/forecast"
ATTRIBUTION = "https://www.earthdata.nasa.gov/data/tools/firms"
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50000
WEATHER_UNITS = {
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "wind_speed_10m": "m/s",
    "wind_gusts_10m": "m/s",
    "precipitation": "mm",
}


class AccessError(RuntimeError):
    """Only fixed, credential-free diagnostics cross the CLI boundary."""


class Availability(DTO):
    product: Product
    min_date: date
    max_date: date

    @model_validator(mode="after")
    def ordered(self) -> "Availability":
        if self.min_date > self.max_date:
            raise ValueError("Availability dates are reversed")
        return self

    def covers(self, start: date, days: int) -> bool:
        return self.min_date <= start <= start + timedelta(days=days - 1) <= self.max_date


class SampleInspection(DTO):
    product: Product
    requested_start: date
    requested_end: date
    sha256: str
    byte_count: int
    row_count: int
    columns: list[str]
    column_types: dict[str, list[str]]
    missing_values: dict[str, int]
    observed_satellites: list[str]
    observed_confidence: list[str]
    observed_versions: list[str]
    first_acquisition: UTCDateTime | None
    last_acquisition: UTCDateTime | None
    rows_by_date: dict[str, int]
    observed_months: list[str]
    empty: bool


class WeatherInspection(DTO):
    requested_latitude: float
    requested_longitude: float
    returned_latitude: float
    returned_longitude: float
    start_date: date
    end_date: date
    model: Literal["gfs_seamless"] = "gfs_seamless"
    product: Literal["historical_forecast"] = "historical_forecast"
    expected_hours: int
    valid_hours: dict[str, int]
    units: dict[str, str]
    complete: bool
    sha256: str


class AccessReport(DTO):
    schema_version: Literal["source-access-v1"] = "source-access-v1"
    retrieved_at: UTCDateTime
    region: str
    bbox: BoundingBox
    availability: list[Availability] = Field(default_factory=list)
    unavailable_products: list[Product] = Field(default_factory=list)
    advertised_months: dict[Product, list[str]] = Field(default_factory=dict)
    request_manifests: dict[str, dict[str, Any]] = Field(default_factory=dict)
    samples: dict[str, SampleInspection] = Field(default_factory=dict)
    weather: list[WeatherInspection] = Field(default_factory=list)
    files_sha256: dict[str, str] = Field(default_factory=dict)
    status: Literal["blocked", "probed"] = "blocked"
    limitations: list[str] = Field(default_factory=list)


def csv_records(content: bytes, required: set[str]) -> tuple[list[str], list[dict[str, str]]]:
    """Inspect real CSV, including header-only no-detection responses; reject error pages."""
    if len(content) > MAX_BYTES:
        raise AccessError("Source response exceeds the 8 MiB reconnaissance limit.")
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")), strict=True)
        columns = reader.fieldnames
        if (
            columns is None
            or len(columns) != len(set(columns))
            or not required <= set(columns)
            or any(not item or len(item) > 100 for item in columns)
        ):
            raise AccessError("Source CSV has missing, duplicate or invalid columns.")
        rows: list[dict[str, str]] = []
        for row in reader:
            if len(rows) >= MAX_ROWS:
                raise AccessError("Source CSV exceeds the 50,000-row reconnaissance limit.")
            if None in row or any(value is None for value in row.values()):
                raise AccessError("Source CSV has inconsistent row widths.")
            rows.append({str(key): str(value) for key, value in row.items()})
        return list(columns), rows
    except (UnicodeError, csv.Error):
        raise AccessError("Source response is not a valid UTF-8 CSV.") from None


def parse_availability(content: bytes) -> list[Availability]:
    _, rows = csv_records(content, {"data_id", "min_date", "max_date"})
    result = []
    seen = set()
    try:
        for row in rows:
            if row["data_id"] not in {item.value for item in Product}:
                continue
            if row["data_id"] in seen:
                raise AccessError("Provider returned duplicate product availability rows.")
            seen.add(row["data_id"])
            result.append(
                Availability(
                    product=Product(row["data_id"]),
                    min_date=date.fromisoformat(row["min_date"]),
                    max_date=date.fromisoformat(row["max_date"]),
                )
            )
    except ValueError:
        raise AccessError("Provider returned invalid availability dates.") from None
    if not result:
        raise AccessError("Provider returned no supported NOAA product availability.")
    return result


def number(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite value")
    return result


def value_type(value: str) -> str:
    if not value.strip():
        return "missing"
    if re.fullmatch(r"[+-]?\d+", value):
        return "integer_text"
    try:
        number(value)
        return "number_text"
    except ValueError:
        return "text"


def inspect_sample(
    content: bytes, product: Product, bbox: BoundingBox, start: date, days: int
) -> SampleInspection:
    if not 1 <= days <= 5:
        raise AccessError("Reconnaissance samples must cover 1–5 days.")
    required = {"latitude", "longitude", "acq_date", "acq_time", "satellite", "confidence", "frp"}
    columns, rows = csv_records(content, required)
    types: dict[str, set[str]] = {column: set() for column in columns}
    missing = dict.fromkeys(columns, 0)
    times = []
    counts: dict[str, int] = {}
    sensor = "20" if "NOAA20" in product else "21"
    satellites = {
        sensor,
        "N" + sensor,
        "NOAA-" + sensor,
        "JPSS-" + ("1" if sensor == "20" else "2"),
    }
    for row in rows:
        try:
            lat, lon = number(row["latitude"]), number(row["longitude"])
            if not bbox.south <= lat <= bbox.north or not bbox.west <= lon <= bbox.east:
                raise ValueError("Outside requested region")
            day = date.fromisoformat(row["acq_date"])
            clock = row["acq_time"]
            if not re.fullmatch(r"\d{1,4}", clock):
                raise ValueError("Invalid acquisition time")
            padded = clock.zfill(4)
            acquired = datetime.combine(day, datetime.strptime(padded, "%H%M").time(), UTC)
            if not start <= day < start + timedelta(days=days):
                raise ValueError("Outside requested date window")
            if row["satellite"].upper() not in satellites:
                raise ValueError("Unexpected sensor")
            if row["frp"].strip() and number(row["frp"]) < 0:
                raise ValueError("Negative FRP")
            for column in ("scan", "track", "bright_ti4", "bright_ti5"):
                if row.get(column, "").strip():
                    numeric = number(row[column])
                    if numeric < 0 or (column.startswith("bright") and numeric == 0):
                        raise ValueError("Invalid optional measurement")
        except ValueError:
            raise AccessError(
                "Sample contains invalid coordinates, times, sensor or measurements."
            ) from None
        times.append(acquired)
        counts[day.isoformat()] = counts.get(day.isoformat(), 0) + 1
        for column, value in row.items():
            types[column].add(value_type(value))
            missing[column] += not value.strip()
    return SampleInspection(
        product=product,
        requested_start=start,
        requested_end=start + timedelta(days=days - 1),
        sha256=hashlib.sha256(content).hexdigest(),
        byte_count=len(content),
        row_count=len(rows),
        columns=columns,
        column_types={key: sorted(value) for key, value in types.items()},
        missing_values=missing,
        observed_satellites=sorted({row["satellite"] for row in rows}),
        observed_confidence=sorted({row["confidence"] for row in rows}),
        observed_versions=sorted({row["version"] for row in rows if row.get("version")}),
        first_acquisition=min(times) if times else None,
        last_acquisition=max(times) if times else None,
        rows_by_date=dict(sorted(counts.items())),
        observed_months=sorted({day[:7] for day in counts}),
        empty=not rows,
    )


def read_response(client: httpx.Client, url: str, key: SecretStr | None = None) -> bytes:
    """No redirects/retries; bounded streaming; never echo response/error bodies or URLs."""
    started = time.monotonic()
    try:
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.status_code == 429:
                raise AccessError("Provider rate limit reached; wait before running another probe.")
            if response.status_code != 200:
                raise AccessError(f"Provider request failed (HTTP {response.status_code}).")
            content = bytearray()
            for chunk in response.iter_bytes():
                if len(content) + len(chunk) > MAX_BYTES:
                    raise AccessError("Source response exceeds the 8 MiB reconnaissance limit.")
                if time.monotonic() - started > 30:
                    raise AccessError("Source response exceeded the reconnaissance time limit.")
                content.extend(chunk)
            result = bytes(content)
            if key is not None and key.get_secret_value().encode() in result:
                raise AccessError("Provider echoed a credential; response was discarded.")
            return result
    except httpx.HTTPError:
        raise AccessError(
            "Provider connection failed; check network access and retry later."
        ) from None


def inspect_weather(
    content: bytes, latitude: float, longitude: float, start: date, days: int
) -> WeatherInspection:
    try:
        data = json.loads(content)
        if data.get("error") or data["utc_offset_seconds"] != 0:
            raise ValueError("Invalid provider/timezone")
        returned_lat = number(str(data["latitude"]))
        returned_lon = number(str(data["longitude"]))
        if abs(latitude - returned_lat) > 0.5 or abs(longitude - returned_lon) > 0.5:
            raise ValueError("Unexpected grid cell")
        hourly = data["hourly"]
        expected = [
            (datetime.combine(start, datetime.min.time()) + timedelta(hours=index)).isoformat(
                timespec="minutes"
            )
            for index in range(days * 24)
        ]
        if hourly["time"] != expected:
            raise ValueError("Missing or unexpected hours")
        valid = {}
        for variable, unit in WEATHER_UNITS.items():
            if data["hourly_units"][variable] != unit or len(hourly[variable]) != len(expected):
                raise ValueError("Unexpected units or array shape")
            count = 0
            for value in hourly[variable]:
                if value is None:
                    continue
                if isinstance(value, bool) or not isinstance(value, int | float):
                    raise ValueError("Invalid weather value")
                numeric = number(str(value))
                if variable == "temperature_2m" and numeric < -273.15:
                    raise ValueError("Invalid temperature")
                if variable != "temperature_2m" and numeric < 0:
                    raise ValueError("Negative weather measurement")
                if variable == "relative_humidity_2m" and numeric > 100:
                    raise ValueError("Invalid humidity")
                count += 1
            valid[variable] = count
        return WeatherInspection(
            requested_latitude=latitude,
            requested_longitude=longitude,
            returned_latitude=returned_lat,
            returned_longitude=returned_lon,
            start_date=start,
            end_date=start + timedelta(days=days - 1),
            expected_hours=len(expected),
            valid_hours=valid,
            units=WEATHER_UNITS,
            complete=all(count == len(expected) for count in valid.values()),
            sha256=hashlib.sha256(content).hexdigest(),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AccessError(
            "Weather response has invalid schema, coordinates, times, units or values."
        ) from None


def store(output: Path, name: str, content: bytes, report: AccessReport) -> None:
    (output / name).write_bytes(content)
    report.files_sha256[name] = hashlib.sha256(content).hexdigest()


def save_report(output: Path, report: AccessReport) -> None:
    (output / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")


def new_output(base: Path) -> Path:
    """A fresh directory per probe preserves earlier evidence and partial failures."""
    output = base / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])
    output.mkdir(parents=True, exist_ok=False)
    return output


def probe_sources(
    region: RegionDefinition,
    output: Path,
    key: SecretStr | None,
    client: httpx.Client,
    weather_only: bool = False,
) -> AccessReport:
    plan = region.source_access
    if plan is None:
        raise AccessError("Selected region has no source-access study configuration.")
    box = region.bbox
    if (box.east - box.west) * (box.north - box.south) > 25:
        raise AccessError("Reconnaissance region must not exceed 25 square degrees.")
    report = AccessReport(retrieved_at=datetime.now(UTC), region=region.name, bbox=box)
    report.limitations = [
        "Point/day weather probes do not establish full-season or every-cell completeness.",
        "Documented incidents support region selection; they are not training labels.",
        "NOAA-21 is comparison-only; it is not pooled into the NOAA-20 training cohort.",
        "No observations, regions or jobs are written to the application database.",
    ]
    try:
        if weather_only:
            report.limitations.append(
                "FIRMS access and downloads were not attempted (weather-only)."
            )
        elif key is None:
            raise AccessError(
                "FIRMS_MAP_KEY is missing. Configure it locally using the official form."
            )
        else:
            token = key.get_secret_value()
            if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token):
                raise AccessError("FIRMS_MAP_KEY has an invalid format; check the local secret.")
            content = read_response(client, f"{FIRMS}/api/data_availability/csv/{token}/ALL", key)
            report.availability = parse_availability(content)
            store(output, "availability.csv", content, report)
            available = {item.product: item for item in report.availability}
            report.unavailable_products = [item for item in Product if item not in available]
            for item in report.availability:
                months = []
                for month in plan.requested_months:
                    first = date.fromisoformat(month + "-01")
                    following = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
                    if item.min_date <= first and following - timedelta(days=1) <= item.max_date:
                        months.append(month)
                report.advertised_months[item.product] = months
            report.limitations.append(
                "Advertised months describe full calendar months within provider date ranges; "
                "they do not prove daily completeness or regional detections."
            )
            live = available.get(plan.live_product)
            if live is None:
                raise AccessError("The primary live product is absent from current availability.")
            recent = min(live.max_date, datetime.now(UTC).date())
            requests = [("recent", plan.live_product, recent, 1)]
            requests.extend(
                (f"historical-{index + 1}", plan.historical_product, window.start_date, window.days)
                for index, window in enumerate(plan.sample_windows)
            )
            for name, product, start, days in requests:
                dates = available.get(product)
                if dates is None or not dates.covers(start, days):
                    raise AccessError(
                        "Sample dates exceed product availability; arrange archive access."
                    )
                coords = f"{box.west},{box.south},{box.east},{box.north}"
                content = read_response(
                    client,
                    f"{FIRMS}/api/area/csv/{token}/{product.value}/{coords}/{days}/{start.isoformat()}",
                    key,
                )
                report.request_manifests[name] = {
                    "provider": "firms",
                    "endpoint": "/api/area/csv/{MAP_KEY}/{PRODUCT}/{BBOX}/{DAYS}/{DATE}",
                    "product": product.value,
                    "bbox": box.model_dump(mode="json"),
                    "start_date": start.isoformat(),
                    "days": days,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "attribution_reference": ATTRIBUTION,
                }
                # Preserve raw source bytes even when subsequent inspection fails.
                store(output, name + ".csv", content, report)
                report.samples[name] = inspect_sample(content, product, box, start, days)
        points = [
            ((box.south + box.north) / 2, (box.west + box.east) / 2),
            (box.south, box.west),
            (box.south, box.east),
            (box.north, box.west),
            (box.north, box.east),
        ]
        for index, window in enumerate(plan.sample_windows):
            for point, (lat, lon) in enumerate(points):
                params: dict[str, str | float] = {
                    "latitude": lat,
                    "longitude": lon,
                    "start_date": window.start_date.isoformat(),
                    "end_date": (window.start_date + timedelta(days=window.days - 1)).isoformat(),
                    "hourly": ",".join(WEATHER_UNITS),
                    "timezone": "GMT",
                    "wind_speed_unit": "ms",
                    "models": "gfs_seamless",
                }
                url = str(httpx.URL(WEATHER, params=params))
                content = read_response(client, url)
                store(output, f"weather-{index + 1}-{point + 1}.json", content, report)
                report.weather.append(
                    inspect_weather(content, lat, lon, window.start_date, window.days)
                )
        if not all(item.complete for item in report.weather):
            raise AccessError(
                "Historical weather includes missing values; region coverage is not proven."
            )
        if weather_only:
            report.status = "blocked"
        elif any(item.empty for item in report.samples.values()):
            report.limitations.append(
                "At least one sample has no detections; it does not prove source outage."
            )
            report.status = "probed"
        else:
            report.status = "probed"
        report.limitations.append(
            "Archive account/request confirmation and human source review remain required."
        )
    except AccessError as exc:
        report.limitations.append(str(exc))
        save_report(output, report)
        raise
    save_report(output, report)
    return report


def archive_inspection(
    path: Path, product: Product, region: RegionDefinition, start: date, days: int
) -> dict[str, Any]:
    """Inspect a small trusted local archive; never import it or mutate the original."""
    with path.open("rb") as stream:
        content = stream.read(MAX_BYTES + 1)
    return {
        "schema_version": "source-sample-inspection-v1",
        "source": "operator-supplied local archive; provenance must be confirmed separately",
        "region": region.name,
        "bbox": region.bbox.model_dump(mode="json"),
        "inspection": inspect_sample(content, product, region.bbox, start, days).model_dump(
            mode="json"
        ),
        "attribution_reference": ATTRIBUTION,
        "imported": False,
    }
