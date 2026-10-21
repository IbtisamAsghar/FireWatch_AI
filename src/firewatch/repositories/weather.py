"""Immutable weather revisions and bounded, retrieval-cutoff-aware PostgreSQL cache."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from firewatch.db.models import SourceAsset, WeatherRecord
from firewatch.schemas.common import Provider
from firewatch.schemas.contracts import WeatherRecordInput, WeatherSeries
from firewatch.sources.open_meteo import (
    GRID,
    NORMALIZER,
    OpenMeteoClient,
    WeatherBatch,
    WeatherRequest,
    parse_weather,
    utc,
)
from firewatch.sources.weather_budget import WeatherError


def save_weather(session: Session, batch: WeatherBatch) -> None:
    """Caller owns a short transaction. Reparse raw evidence before accepting DTOs."""
    import json

    if not session.in_transaction():
        raise ValueError("Use a caller-owned write transaction")
    batch = WeatherBatch.model_validate(batch.model_dump())
    with Path(batch.asset.storage_path).open("rb") as source:
        raw = source.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != batch.asset.content_hash:
        raise WeatherError("Weather raw evidence checksum mismatch.")
    request = WeatherRequest.model_validate(batch.asset.request_metadata["request"])
    if (
        batch.asset.provider != Provider.OPEN_METEO
        or batch.asset.product != request.product
        or batch.asset.processing_version != NORMALIZER
        or batch.asset.request_metadata.get("grid_version") != GRID
        or batch.asset.request_metadata.get("cache_namespace") != request.source_version
    ):
        raise WeatherError("Weather source metadata mismatch.")
    revision = f"{request.source_version}-{batch.asset_id.hex}"
    expected = parse_weather(
        json.loads(raw), request, batch.asset.retrieved_at, batch.asset_id, revision
    )
    if expected != batch.series:
        raise WeatherError("Weather rows do not match preserved raw evidence.")
    existing = session.get(SourceAsset, batch.asset_id)
    if existing is not None:
        if any(getattr(existing, key) != value for key, value in batch.asset.model_dump().items()):
            raise WeatherError("Weather source identity conflict.")
    else:
        session.add(SourceAsset(id=batch.asset_id, **batch.asset.model_dump()))
        session.flush()
    # ON CONFLICT makes identical artifact retries idempotent; changed fetches get new versions.
    for start in range(0, len(batch.series.records), 250):
        rows = [row.model_dump() for row in batch.series.records[start : start + 250]]
        session.execute(
            insert(WeatherRecord)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_weather_lookup")
        )


def cached_weather(
    session: Session,
    request: WeatherRequest,
    retrieval_cutoff: datetime,
    *,
    oldest_retrieval: datetime | None = None,
) -> WeatherSeries | None:
    cutoff = utc(retrieval_cutoff)
    query = (
        select(WeatherRecord)
        .join(SourceAsset, SourceAsset.id == WeatherRecord.asset_id)
        .where(
            WeatherRecord.provider == Provider.OPEN_METEO,
            WeatherRecord.model == request.model,
            WeatherRecord.product == request.product,
            WeatherRecord.cell_key.in_([c.key for c in request.cells]),
            WeatherRecord.valid_at >= request.start,
            WeatherRecord.valid_at <= request.end,
            WeatherRecord.retrieved_at <= cutoff,
            SourceAsset.processing_version == NORMALIZER,
            SourceAsset.request_metadata["grid_version"].astext == GRID,
            SourceAsset.request_metadata["cache_namespace"].astext == request.source_version,
        )
    )
    if oldest_retrieval is not None:
        query = query.where(WeatherRecord.retrieved_at >= utc(oldest_retrieval))
    query = query.order_by(WeatherRecord.retrieved_at.desc(), WeatherRecord.source_version.desc())
    # Retain one complete source revision per cell, never stitch corrected partial responses.
    wanted = set(request.hours())
    candidates: dict[tuple[str, str], dict[datetime, WeatherRecordInput]] = {}
    for index, row in enumerate(session.scalars(query.limit(100001))):
        if index == 100000:
            raise WeatherError("Weather cache revision bound exceeded; narrow the request.")
        candidates.setdefault((row.cell_key, row.source_version), {})[row.valid_at] = (
            WeatherRecordInput.model_validate(row)
        )
    records: list[WeatherRecordInput] = []
    for cell in request.cells:
        versions = [
            rows for (key, _), rows in candidates.items() if key == cell.key and set(rows) == wanted
        ]
        if not versions:
            return None
        chosen = versions[0]
        records.extend(chosen[t] for t in request.hours())
    return WeatherSeries(records=records)


def get_weather(
    engine: Engine,
    request: WeatherRequest,
    *,
    retrieval_cutoff: datetime | None = None,
    client: OpenMeteoClient | None = None,
    offline: bool = False,
    live_cache_max_age: timedelta = timedelta(hours=1),
) -> WeatherSeries:
    """Network runs outside DB transactions; replay reads historical products only.

    retrieval_cutoff is availability time, separate from the historical valid-time interval.
    Offline replay may use later-retrieved retrospective archives without claiming backtesting.
    """
    request = WeatherRequest.model_validate(request.model_dump())
    cutoff = (
        utc(retrieval_cutoff)
        if retrieval_cutoff is not None
        else utc(client.now() if client is not None else datetime.now(UTC))
    )
    if live_cache_max_age <= timedelta(0):
        raise ValueError("Positive live cache age required")
    oldest = cutoff - live_cache_max_age if request.product == "live" else None
    with Session(engine) as session:
        cached = cached_weather(session, request, cutoff, oldest_retrieval=oldest)
    if cached is not None:
        return cached
    if offline or client is None:
        raise WeatherError("Required weather cache interval is unavailable offline.")
    if retrieval_cutoff is not None and cutoff < utc(client.now()):
        raise WeatherError("A frozen availability cutoff cannot fetch new weather.")
    batch = client.fetch(request)
    if retrieval_cutoff is not None and batch.asset.retrieved_at > cutoff:
        raise WeatherError("Fetched weather is newer than the availability cutoff.")
    with Session(engine) as session, session.begin():
        save_weather(session, batch)
    return batch.series
