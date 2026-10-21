"""Synthetic Open-Meteo fixtures; no real provider measurements."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from firewatch.sources.open_meteo import VARIABLES, WeatherRequest, parse_weather, weather_cell

T = datetime(2024, 7, 25, tzinfo=UTC)
NOW = datetime(2026, 10, 21, tzinfo=UTC)


def request(**kwargs):
    values = dict(cells=[weather_cell(39.4, -121.4)], start=T, end=T + timedelta(hours=24))
    return WeatherRequest(**(values | kwargs))


def payload(req):
    result = []
    for cell in req.cells:
        times = req.hours()
        result.append(
            dict(
                latitude=cell.latitude + 0.01,
                longitude=cell.longitude - 0.01,
                timezone="GMT",
                utc_offset_seconds=0,
                hourly_units={"time": "iso8601", **{k: v[1] for k, v in VARIABLES.items()}},
                hourly={
                    "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
                    **{k: [1.0] * len(times) for k in VARIABLES},
                },
            )
        )
    return result[0] if len(result) == 1 else result


def series(req=None):
    req = req or request()
    return parse_weather(payload(req), req, NOW, uuid4(), "synthetic-v1")
