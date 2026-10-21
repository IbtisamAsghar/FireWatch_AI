"""Callable FIRMS-only refresh stage for the future job worker and operator CLI."""

import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4, uuid5

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from firewatch.db.models import Region, SourceAsset
from firewatch.repositories.refresh import (
    complete_attempt,
    fail_attempt,
    load_refresh,
    reserve_attempt,
    save_refresh,
)
from firewatch.repositories.revisions import RevisionPolicy, select_revisions
from firewatch.schemas.common import Product, Sensor
from firewatch.schemas.contracts import BoundingBox
from firewatch.schemas.firms_refresh import (
    REFRESH_VERSION,
    RefreshChunk,
    RefreshCounts,
    RefreshSpec,
    RefreshState,
    RefreshSummary,
    summarize_refresh,
)
from firewatch.sources.archive_stage import atomic_json
from firewatch.sources.firms import MESSAGES, FirmsAreaRequest, FirmsBatch, FirmsError
from firewatch.sources.firms_normalize import load_saved_firms_batch, normalize_firms_batch


class RefreshClient(Protocol):
    output_dir: Path

    def fetch(self, request: FirmsAreaRequest, *, batch_id: UUID | None = None) -> FirmsBatch: ...


def _clock(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Refresh clock must be timezone-aware")
    return value.astimezone(UTC)


def _fail(
    connection: Connection,
    state: RefreshState,
    chunk: RefreshChunk,
    folder: Path,
    finished: datetime,
    code: str,
    retry_seconds: float | None = None,
) -> None:
    if code not in MESSAGES and code not in {"INTERRUPTED", "NORMALIZATION", "DATABASE"}:
        raise ValueError("Unrecognized fixed refresh error code")
    chunk.status = "failed"
    chunk.error_code = code
    chunk.completed_at = finished
    chunk.counts = None
    chunk.latest_acquisition_at = None
    chunk.retry_at = None
    if retry_seconds is not None and math.isfinite(retry_seconds) and retry_seconds > 0:
        try:
            chunk.retry_at = finished + timedelta(seconds=retry_seconds)
        except OverflowError:
            chunk.retry_at = datetime.max.replace(tzinfo=UTC)
    state.updated_at = finished
    with Session(connection, expire_on_commit=False) as session, session.begin():
        fail_attempt(session, chunk, finished, code)
        save_refresh(session, state, folder)
    atomic_json(folder / "progress.json", state.model_dump(mode="json"))


def _publish_source(
    connection: Connection,
    state: RefreshState,
    chunk: RefreshChunk,
    folder: Path,
    raw: FirmsBatch,
    finished: datetime,
) -> None:
    normalized = normalize_firms_batch(raw, state.spec.normalization_policy)
    with Session(connection, expire_on_commit=False) as session, session.begin():
        result = complete_attempt(session, state, chunk, normalized)
        chunk.status = "succeeded"
        chunk.completed_at = raw.retrieved_at
        chunk.retry_at = None
        chunk.error_code = None
        chunk.counts = RefreshCounts(
            fetched=result.fetched_count,
            accepted=result.inserted_count,
            rejected=result.rejected_count,
            duplicate=result.duplicate_count,
        )
        chunk.latest_acquisition_at = max(
            (
                r.observation.acquired_at
                for r in normalized.revisions
                if state.spec.window_start <= r.observation.acquired_at <= state.spec.as_of
            ),
            default=None,
        )
        state.updated_at = finished
        save_refresh(session, state, folder)


def _handoff(connection: Connection, state: RefreshState, folder: Path) -> RefreshSummary:
    summary = summarize_refresh(state)
    target = folder / "normalized-records.jsonl"
    temporary = folder / ("records.tmp-" + uuid4().hex)
    hasher = hashlib.sha256()
    count = 0
    try:
        with temporary.open("wb") as output, Session(connection) as session:
            for chunk in state.chunks:
                if chunk.status != "succeeded" or chunk.batch_id is None:
                    continue
                sensor = (
                    Sensor.NOAA20 if chunk.request.product == Product.NOAA20_NRT else Sensor.NOAA21
                )
                rows = select_revisions(
                    session,
                    state.region_id,
                    state.spec.window_start,
                    state.spec.as_of + timedelta(microseconds=1),
                    RevisionPolicy(
                        products={sensor: chunk.request.product},
                        batch_ids=[chunk.batch_id],
                        retrieval_cutoff=state.updated_at,
                        allow_rejected_batches=True,
                        max_observations=100000,
                    ),
                )
                for row in rows:
                    line = (row.model_dump_json() + "\n").encode()
                    output.write(line)
                    hasher.update(line)
                    count += 1
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    atomic_json(
        folder / "handoff.json",
        {
            "schema_version": "firms-refresh-handoff-v1",
            "summary": summary.model_dump(mode="json"),
            "state": state.model_dump(mode="json"),
            "normalized_records_path": str(target),
            "normalized_records_sha256": hasher.hexdigest(),
            "normalized_records_count": count,
            "source_batch_ids": [str(c.batch_id) for c in state.chunks if c.status == "succeeded"],
            "selection_window": (
                "inclusive 48-hour acquisition window; provider request counts "
                "cover full UTC calendar dates"
            ),
            "partial_coverage_is_not_zero_fires": True,
            "pipeline_run_completed": False,
        },
    )
    return summary


def refresh_firms(
    engine: Engine,
    client: RefreshClient | None,
    spec: RefreshSpec | None = None,
    *,
    resume: UUID | None = None,
    output: Path = Path("data/firms-refresh"),
    allow_disabled: bool = False,
    max_age_hours: int = 72,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RefreshSummary:
    if (spec is None) == (resume is None):
        raise ValueError("Provide either a new refresh specification or a saved refresh ID")
    clock = _clock(now)
    if spec is not None:
        spec = RefreshSpec.model_validate(spec.model_dump(mode="json"))
        if spec.as_of > clock:
            raise ValueError("Live refresh cutoff exceeds its allowed age or is in the future")
    with engine.connect() as connection:
        with Session(connection, expire_on_commit=False) as session, session.begin():
            if resume is not None:
                asset, state = load_refresh(session, resume)
                area = session.get(Region, state.region_id)
                folder = Path(asset.storage_path).parent
            else:
                assert spec is not None
                area = session.scalar(select(Region).where(Region.name == spec.region))
                if area is None:
                    raise ValueError("Unknown configured refresh region")
                bbox = BoundingBox(
                    west=area.west, south=area.south, east=area.east, north=area.north
                )
                identifier = spec.identity(area.id)
                folder = output.resolve() / str(identifier)
                state = RefreshState(
                    refresh_id=identifier,
                    region_id=area.id,
                    bbox=bbox,
                    spec=spec,
                    updated_at=clock,
                    chunks=[
                        RefreshChunk(key=str(i), request=r)
                        for i, r in enumerate(spec.requests(bbox))
                    ],
                )
        if area is None or (not area.enabled and not allow_disabled):
            raise ValueError(
                "Source refresh requires an enabled region or explicit disabled-region validation"
            )
        if (area.west, area.south, area.east, area.north) != (
            state.bbox.west,
            state.bbox.south,
            state.bbox.east,
            state.bbox.north,
        ):
            raise ValueError("Saved refresh region geometry changed")
        key = int.from_bytes(uuid5(state.region_id, REFRESH_VERSION).bytes[:8], "big", signed=True)
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": key})
        connection.commit()
        if not acquired:
            raise ValueError("Another FIRMS refresh owns this region; retry later")
        try:
            with Session(connection, expire_on_commit=False) as session, session.begin():
                existing = session.get(SourceAsset, state.refresh_id)
                if existing is not None:
                    asset, saved = load_refresh(session, state.refresh_id, locked=True)
                    if (
                        saved.spec != state.spec
                        or saved.region_id != state.region_id
                        or saved.bbox != state.bbox
                    ):
                        raise ValueError("Refresh idempotency key conflicts with another request")
                    state = saved
                    folder = Path(asset.storage_path).parent
                else:
                    if clock - state.spec.as_of > timedelta(hours=max_age_hours):
                        raise ValueError("Live refresh cutoff is too old")
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / "request.json").write_text(
                        json.dumps(
                            state.spec.model_dump(mode="json"),
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8",
                    )
                    save_refresh(session, state, folder)
            for chunk in state.chunks:
                clock = _clock(now)
                if chunk.status == "succeeded":
                    continue
                if (
                    chunk.status == "failed"
                    and chunk.retry_at is not None
                    and chunk.retry_at > clock
                ):
                    continue
                if chunk.status == "running":
                    try:
                        raw = load_saved_firms_batch(Path(chunk.fetch_manifest_path or ""))
                    except (OSError, ValueError, KeyError, IndexError, TypeError, FirmsError):
                        _fail(connection, state, chunk, folder, clock, "INTERRUPTED")
                    else:
                        try:
                            _publish_source(connection, state, chunk, folder, raw, clock)
                        except (ValueError, OSError):
                            _fail(connection, state, chunk, folder, clock, "NORMALIZATION")
                        except SQLAlchemyError:
                            _fail(connection, state, chunk, folder, clock, "DATABASE", 60)
                        continue
                if client is None:
                    raise ValueError(
                        "Configure FIRMS_MAP_KEY before fetching pending source chunks"
                    )
                if clock - state.spec.as_of > timedelta(hours=max_age_hours):
                    raise ValueError(
                        "Start a new live refresh; this saved cutoff is too old to refetch"
                    )
                chunk.status = "running"
                chunk.attempts += 1
                chunk.fetch_id = uuid4()
                chunk.attempted_at = clock
                chunk.completed_at = None
                chunk.counts = None
                chunk.retry_at = None
                chunk.error_code = None
                chunk.latest_acquisition_at = None
                chunk.fetch_manifest_path = str(
                    client.output_dir / str(chunk.fetch_id) / "manifest.json"
                )
                attempt = folder / ("attempt-" + str(chunk.fetch_id) + ".json")
                attempt.write_text(
                    json.dumps(
                        chunk.request.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                    ),
                    encoding="utf-8",
                )
                state.updated_at = clock
                with Session(connection, expire_on_commit=False) as session, session.begin():
                    reserve_attempt(session, state, chunk, attempt)
                    save_refresh(session, state, folder)
                try:
                    raw = client.fetch(chunk.request, batch_id=chunk.fetch_id)
                    _publish_source(connection, state, chunk, folder, raw, _clock(now))
                except FirmsError as error:
                    delay = (
                        error.retry_after_seconds
                        if error.retry_after_seconds is not None
                        else 60
                        if error.retryable
                        else None
                    )
                    _fail(connection, state, chunk, folder, _clock(now), error.code, delay)
                except (ValueError, OSError):
                    _fail(connection, state, chunk, folder, _clock(now), "NORMALIZATION")
                except SQLAlchemyError:
                    _fail(connection, state, chunk, folder, _clock(now), "DATABASE", 60)
            atomic_json(folder / "progress.json", state.model_dump(mode="json"))
            return _handoff(connection, state, folder)
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            connection.commit()
