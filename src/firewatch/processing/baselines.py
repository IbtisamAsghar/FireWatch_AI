"""Fixed-reference daily climate statistics and honest hourly-weather departures."""

import calendar
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from pydantic import Field, model_validator

from firewatch.processing.weather import weather_at_cutoff
from firewatch.schemas.common import DTO, UTCDateTime
from firewatch.schemas.contracts import ClimateBaseline, WeatherSeries
from firewatch.sources.open_meteo import utc
from firewatch.sources.power import GRID, ClimateCell, PowerBatch, climate_cell

BASELINE_PROCESSOR = "power-monthly-v1"


class BaselineSpec(DTO):
    reference_start_year: int = Field(default=2010, ge=1981, le=2090)
    reference_end_year: int = Field(default=2019, ge=1981, le=2099)
    earliest_snapshot: UTCDateTime = datetime(2022, 5, 1, tzinfo=UTC)
    version: str = Field(default=BASELINE_PROCESSOR, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    min_valid_fraction: float = Field(default=0.9, gt=0, le=1)

    @model_validator(mode="after")
    def reference_is_past(self) -> Self:
        if not 10 <= self.reference_end_year - self.reference_start_year + 1 <= 30:
            raise ValueError("Use ten to thirty complete reference years")
        if datetime(self.reference_end_year + 1, 1, 1, tzinfo=UTC) > self.earliest_snapshot:
            raise ValueError("Reference period must end before the earliest snapshot")
        return self


def load_baseline_spec(path: Path) -> BaselineSpec:
    with path.open("rb") as source:
        content = source.read(65537)
    if len(content) > 65536:
        raise ValueError("Feature config is too large")
    config = yaml.safe_load(content)
    if not isinstance(config, dict) or config.get("schema_version") != "features-v1":
        raise ValueError("Expected features-v1 configuration")
    years = config["climate_reference_years"]
    return BaselineSpec(
        reference_start_year=years["start"],
        reference_end_year=years["end"],
        earliest_snapshot=config["climate_earliest_snapshot"],
        version=config["climate_baseline_version"],
        min_valid_fraction=config["climate_min_valid_fraction"],
    )


def compute_baselines(
    batches: list[PowerBatch], spec: BaselineSpec, asset_id: UUID
) -> list[ClimateBaseline]:
    spec = BaselineSpec.model_validate(spec.model_dump())
    required = set(range(spec.reference_start_year, spec.reference_end_year + 1))
    if (
        len(batches) != len(required)
        or {b.request.year for b in batches} != required
        or len({b.request.cell.key for b in batches}) != 1
    ):
        raise ValueError("Use one unique complete set of reference years for one cell")
    cell = batches[0].request.cell
    for batch in batches:
        if [r.day for r in batch.days] != batch.request.days() or batch.request.cell != cell:
            raise ValueError("Daily coverage or climate cell does not match reference")
    results = []
    for month in range(1, 13):
        rows = [
            row
            for batch in sorted(batches, key=lambda b: b.request.year)
            for row in batch.days
            if row.day.month == month
        ]
        expected = sum(calendar.monthrange(year, month)[1] for year in required)
        flags = []
        means: dict[str, Any] = {}
        counts = {"expected_days": expected}
        for field, target in [
            ("temperature_c", "temperature_mean_c"),
            ("precipitation_mm", "precipitation_mean_mm"),
        ]:
            values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
            if any(
                not math.isfinite(v) or v < (-273.15 if field == "temperature_c" else 0)
                for v in values
            ):
                raise ValueError("Invalid daily reference values")
            counts[field] = len(values)
            counts[field + "_years"] = len(
                {r.day.year for r in rows if getattr(r, field) is not None}
            )
            sufficient = len(values) >= math.ceil(spec.min_valid_fraction * expected)
            means[target] = math.fsum(values) / len(values) if sufficient else None
            if len(values) < expected:
                flags.append(field + "_reference_incomplete")
            if not sufficient:
                flags.append(field + "_baseline_missing")
        statistics: dict[str, Any] = {
            "processor": BASELINE_PROCESSOR,
            "grid_version": GRID,
            "coarse_resolution_degrees": [0.5, 0.625],
            "grid_meaning": "application coarse request bins; API point coordinates echo request",
            "time_standard": "UTC",
            "daily_interval": "[00:00Z,next-day 00:00Z)",
            "aggregation": "arithmetic mean of valid daily values pooled by UTC calendar month",
            "temperature_definition": "monthly mean daily temperature; not hourly z-score",
            "precipitation_definition": "monthly reference mean daily precipitation in mm/day",
            "min_valid_fraction": spec.min_valid_fraction,
            "quality_flags": flags,
            "units": {"temperature_mean_c": "C", "precipitation_mean_mm": "mm/day"},
            "earliest_snapshot": spec.earliest_snapshot.isoformat(),
            "source_assets": [
                str(b.asset_id) for b in sorted(batches, key=lambda b: b.request.year)
            ],
            "source_hashes": [
                b.asset.content_hash for b in sorted(batches, key=lambda b: b.request.year)
            ],
        }
        results.append(
            ClimateBaseline(
                cell_key=cell.key,
                latitude=cell.latitude,
                longitude=cell.longitude,
                reference_start_year=spec.reference_start_year,
                reference_end_year=spec.reference_end_year,
                month=month,
                sample_counts=counts,
                statistics=statistics,
                version=spec.version,
                asset_id=asset_id,
                **means,
            )
        )
    return results


def baseline_manifest(batches: list[PowerBatch], spec: BaselineSpec) -> bytes:
    rows = compute_baselines(batches, spec, UUID(int=0))
    body = {
        "schema_version": BASELINE_PROCESSOR,
        "spec": spec.model_dump(mode="json"),
        "baselines": [row.model_dump(mode="json", exclude={"asset_id"}) for row in rows],
    }
    return (
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def baseline_asset_id(content: bytes) -> UUID:
    return uuid5(NAMESPACE_URL, BASELINE_PROCESSOR + ":" + hashlib.sha256(content).hexdigest())


class ClimateContext(DTO):
    as_of: UTCDateTime
    temperature_departure_c: float | None
    rainfall_deficit_24h_mm: float | None
    baseline_version: str | None
    baseline_asset_id: UUID | None
    precipitation_interval_end: UTCDateTime
    temperature_definition: str = (
        "departure from monthly mean daily climate reference; not hourly z-score"
    )
    quality_flags: list[str]


def climate_context(
    series: WeatherSeries,
    as_of: datetime,
    cell: ClimateCell,
    baseline: ClimateBaseline | None,
    *,
    weather_max_age_hours: float = 3,
) -> ClimateContext:
    cutoff = utc(as_of)
    weather = weather_at_cutoff(series, cutoff, max_age_hours=weather_max_age_hours)
    if any(
        climate_cell(r.requested_latitude, r.requested_longitude) != cell for r in series.records
    ):
        raise ValueError("Weather does not belong to selected climate cell")
    flags = list(weather.quality_flags)
    temperature = None
    rainfall = None
    if baseline is None:
        flags.append("climate_baseline_missing")
    else:
        baseline = ClimateBaseline.model_validate(baseline.model_dump())
        if (
            baseline.source != "nasa_power"
            or baseline.cell_key != cell.key
            or (baseline.latitude, baseline.longitude) != (cell.latitude, cell.longitude)
            or baseline.month != cutoff.month
            or datetime(baseline.reference_end_year + 1, 1, 1, tzinfo=UTC) > cutoff
            or baseline.statistics.get("processor") != BASELINE_PROCESSOR
            or baseline.statistics.get("time_standard") != "UTC"
        ):
            raise ValueError("Climate baseline cell, month, convention or cutoff mismatch")
        baseline_flags = baseline.statistics.get("quality_flags", [])
        if not isinstance(baseline_flags, list) or any(
            not isinstance(f, str) for f in baseline_flags
        ):
            raise ValueError("Invalid baseline quality metadata")
        flags.extend(str(flag) for flag in baseline_flags)
        if baseline.temperature_mean_c is not None and weather.record is not None:
            value = weather.record.temperature_c
            if value is not None:
                temperature = value - baseline.temperature_mean_c
        if baseline.precipitation_mean_mm is not None and weather.precipitation_24h_mm is not None:
            rainfall = max(0.0, baseline.precipitation_mean_mm - weather.precipitation_24h_mm)
    if temperature is None:
        flags.append("temperature_departure_missing")
    if rainfall is None:
        flags.append("rainfall_deficit_missing")
    return ClimateContext(
        as_of=cutoff,
        temperature_departure_c=temperature,
        rainfall_deficit_24h_mm=rainfall,
        baseline_version=baseline.version if baseline else None,
        baseline_asset_id=baseline.asset_id if baseline else None,
        precipitation_interval_end=weather.precipitation_interval_end,
        quality_flags=sorted(set(flags)),
    )
