"""Past-only weather selection and complete preceding-day rainfall semantics."""

from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from firewatch.schemas.common import DTO, UTCDateTime
from firewatch.schemas.contracts import WeatherRecordInput, WeatherSeries
from firewatch.sources.open_meteo import HOUR, utc


class WeatherAtCutoff(DTO):
    as_of: UTCDateTime
    record: WeatherRecordInput | None
    precipitation_24h_mm: float | None
    weather_age_hours: float | None
    precipitation_interval_end: UTCDateTime
    quality_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def past(self) -> Self:
        if self.record is not None and self.record.valid_at > self.as_of:
            raise ValueError("Selected weather cannot follow cutoff")
        return self


def weather_at_cutoff(
    series: WeatherSeries,
    as_of: datetime,
    *,
    max_age_hours: float = 3,
) -> WeatherAtCutoff:
    """All values refer to one cell/product/model; no silent product fallback or imputation.

    For non-hour T, rolling rainfall ends at floor(T), the last completed UTC hour.
    Required hourly sums end at floor(T)-23h,...,floor(T), covering exactly 24 hours.
    """
    import math

    cutoff = utc(as_of)
    if not math.isfinite(max_age_hours) or max_age_hours <= 0:
        raise ValueError("Positive finite weather age required")
    end = cutoff.replace(minute=0, second=0, microsecond=0)
    identities = {
        (r.cell_key, r.provider, r.product, r.model, r.source_version) for r in series.records
    }
    if len(identities) > 1:
        raise ValueError("Select one cell, model, product and source revision")
    rows = {r.valid_at: r for r in series.records}
    if len(rows) != len(series.records):
        raise ValueError("Weather timestamps must be unique")
    eligible = [
        r
        for r in rows.values()
        if r.valid_at <= end
        and any(
            getattr(r, name) is not None
            for name in ("temperature_c", "humidity_pct", "wind_ms", "gust_ms", "precipitation_mm")
        )
    ]
    record = max(eligible, key=lambda r: r.valid_at) if eligible else None
    flags = list(series.quality_flags)
    age = (cutoff - record.valid_at).total_seconds() / 3600 if record else None
    if record:
        flags.extend(record.quality_flags)
        if age is not None and age > max_age_hours:
            flags.append("weather_stale")
        if record.valid_at < end:
            flags.append("weather_latest_hour_missing")
    else:
        flags.append("weather_missing")
    rain = 0.0
    complete = True
    for offset in range(24):
        row = rows.get(end - offset * HOUR)
        if row is None or row.precipitation_mm is None:
            complete = False
        else:
            rain += row.precipitation_mm
            flags.extend(row.quality_flags)
    if not complete:
        flags.append("precipitation_24h_incomplete")
    flags.append("precipitation_24h_ends_at_completed_hour")
    return WeatherAtCutoff(
        as_of=cutoff,
        record=record,
        precipitation_24h_mm=rain if complete else None,
        weather_age_hours=age,
        precipitation_interval_end=end,
        quality_flags=sorted(set(flags)),
    )
