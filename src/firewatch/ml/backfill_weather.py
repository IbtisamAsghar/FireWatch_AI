"""Bounded historical weather fill plans derived from verified backfill source proofs."""

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from firewatch.ml.backfill import _read, verify_chunk
from firewatch.processing.features import FeatureSpec
from firewatch.repositories.weather import get_weather
from firewatch.schemas.common import RunMode
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import OpenMeteoClient, WeatherCell, WeatherRequest
from firewatch.sources.weather_budget import WeatherError


def weather_plan(root: Path) -> list[WeatherRequest]:
    job = _read(root / "job.json", 1024 * 1024)
    config = FeatureSpec.model_validate(job["feature_spec"])
    by_week: dict[datetime, dict[str, WeatherCell]] = defaultdict(dict)
    for index, offset in enumerate(range(0, len(job["cutoffs"]), job["chunk_size"])):
        chunk = root / f"chunk-{index:04d}"
        manifest = _read(chunk / "manifest.json", 1024 * 1024)
        if (
            manifest["job_sha256"] != digest(job)
            or manifest["cutoffs"] != job["cutoffs"][offset : offset + job["chunk_size"]]
        ):
            raise ValueError("Weather fill requires the complete original backfill collection")
        verify_chunk(chunk)
        for raw in _read(chunk / "weather-requests.json"):
            request = WeatherRequest.model_validate(raw)
            if (
                request.mode != RunMode.REPLAY
                or request.product != config.replay_weather_product
                or request.model != config.replay_weather_model
                or request.source_version != config.weather_namespace
            ):
                raise ValueError("Weather fill request differs from frozen historical policy")
            day = request.start.replace(hour=0)
            while day <= request.end:
                week = day - timedelta(days=day.weekday())
                for cell in request.cells:
                    by_week[week][cell.key] = cell
                day += timedelta(days=1)
    result = []
    for week, mapping in sorted(by_week.items()):
        cells = [mapping[k] for k in sorted(mapping)]
        for offset in range(0, len(cells), 4):
            # Four cells * seven days = 28 weighted units, within the existing 30-unit bound.
            result.append(
                WeatherRequest(
                    cells=cells[offset : offset + 4],
                    start=week,
                    end=week + timedelta(hours=167),
                    product=config.replay_weather_product,
                    model=config.replay_weather_model,
                    source_version=config.weather_namespace,
                    mode=RunMode.REPLAY,
                )
            )
    return result


def fill_weather(
    engine: Engine,
    requests: list[WeatherRequest],
    client: OpenMeteoClient,
    *,
    offset: int = 0,
    max_requests: int = 1,
) -> dict[str, Any]:
    if not 0 <= offset <= len(requests) or not 1 <= max_requests <= 20:
        raise ValueError("Invalid weather fill offset or bound")
    completed = 0
    for request in requests[offset : offset + max_requests]:
        try:
            get_weather(engine, request, client=client)
        except WeatherError:
            # Provider bodies and connection details are never exposed.
            return dict(
                status="paused",
                reason="provider_or_budget_unavailable",
                completed_requests=completed,
                next_offset=offset + completed,
                total_requests=len(requests),
            )
        completed += 1
    return dict(
        status="complete" if offset + completed == len(requests) else "bounded_batch_complete",
        completed_requests=completed,
        next_offset=offset + completed,
        total_requests=len(requests),
    )
