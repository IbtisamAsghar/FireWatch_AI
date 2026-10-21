"""Resumable annual POWER source cache and immutable monthly baseline publication."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from firewatch.db.models import ClimateBaselineRecord, SourceAsset
from firewatch.processing.baselines import (
    BASELINE_PROCESSOR,
    BaselineSpec,
    baseline_asset_id,
    baseline_manifest,
    compute_baselines,
)
from firewatch.schemas.contracts import ClimateBaseline, SourceAssetInput
from firewatch.sources.open_meteo import utc
from firewatch.sources.power import (
    VERSION,
    ClimateCell,
    PowerBatch,
    PowerClient,
    PowerRequest,
    batch_from_raw,
)
from firewatch.sources.weather_budget import WeatherError


def save_power_batch(session: Session, batch: PowerBatch) -> None:
    if not session.in_transaction():
        raise ValueError("Use a caller-owned transaction")
    if batch_from_raw(batch.asset_id, batch.asset) != batch:
        raise WeatherError("POWER normalized rows do not match source evidence.")
    session.execute(
        insert(SourceAsset)
        .values(id=batch.asset_id, **batch.asset.model_dump())
        .on_conflict_do_nothing(index_elements=[SourceAsset.id])
    )
    saved = session.get(SourceAsset, batch.asset_id)
    if saved is None or any(getattr(saved, k) != v for k, v in batch.asset.model_dump().items()):
        raise WeatherError("POWER source identity conflict.")


def cached_power(session: Session, request: PowerRequest, cutoff: datetime) -> PowerBatch | None:
    source = session.scalar(
        select(SourceAsset)
        .where(
            SourceAsset.provider == "nasa_power",
            SourceAsset.product == VERSION,
            SourceAsset.processing_version == VERSION,
            SourceAsset.retrieved_at <= utc(cutoff),
            SourceAsset.request_metadata["request"]["cell"]["key"].astext == request.cell.key,
            SourceAsset.request_metadata["request"]["year"].as_integer() == request.year,
        )
        .order_by(SourceAsset.retrieved_at.desc(), SourceAsset.id.desc())
        .limit(1)
    )
    if source is None:
        return None
    batch = batch_from_raw(source.id, SourceAssetInput.model_validate(source))
    if batch.request != request:
        raise WeatherError("POWER cache request mismatch.")
    return batch


def cached_baselines(
    session: Session, cell: ClimateCell, spec: BaselineSpec, cutoff: datetime
) -> list[ClimateBaseline] | None:
    rows = list(
        session.scalars(
            select(ClimateBaselineRecord)
            .join(SourceAsset, SourceAsset.id == ClimateBaselineRecord.asset_id)
            .where(
                ClimateBaselineRecord.cell_key == cell.key,
                ClimateBaselineRecord.source == "nasa_power",
                ClimateBaselineRecord.reference_start_year == spec.reference_start_year,
                ClimateBaselineRecord.reference_end_year == spec.reference_end_year,
                ClimateBaselineRecord.version == spec.version,
                SourceAsset.retrieved_at <= utc(cutoff),
            )
            .order_by(ClimateBaselineRecord.month)
        )
    )
    if not rows:
        return None
    if len(rows) != 12 or {r.month for r in rows} != set(range(1, 13)):
        raise WeatherError("Incomplete climate baseline publication.")
    result = [ClimateBaseline.model_validate(r) for r in rows]
    if len({r.asset_id for r in result}) != 1:
        raise WeatherError("Climate baseline publication mixes source manifests.")
    asset = session.get(SourceAsset, result[0].asset_id)
    if (
        asset is None
        or asset.processing_version != BASELINE_PROCESSOR
        or asset.provider != "nasa_power"
        or asset.product != BASELINE_PROCESSOR
    ):
        raise WeatherError("Climate baseline manifest is unavailable.")
    with Path(asset.storage_path).open("rb") as source:
        content = source.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024 or hashlib.sha256(content).hexdigest() != asset.content_hash:
        raise WeatherError("Climate baseline manifest checksum mismatch.")
    try:
        manifest = json.loads(content)
        if manifest["spec"] != spec.model_dump(mode="json") or manifest["baselines"] != [
            r.model_dump(mode="json", exclude={"asset_id"}) for r in result
        ]:
            raise WeatherError("Climate baseline manifest or definition mismatch.")
    except (ValueError, TypeError, KeyError):
        raise WeatherError("Climate baseline manifest is invalid.") from None
    for row in result:
        if (
            row.statistics.get("min_valid_fraction") != spec.min_valid_fraction
            or row.statistics.get("earliest_snapshot") != spec.earliest_snapshot.isoformat()
            or row.statistics.get("processor") != BASELINE_PROCESSOR
        ):
            raise WeatherError("Baseline version is already bound to a different definition.")
    return result


def publish_baselines(
    session: Session, batches: list[PowerBatch], spec: BaselineSpec, output_dir: Path
) -> list[ClimateBaseline]:
    if not session.in_transaction():
        raise ValueError("Use a caller-owned transaction")
    # Trust database-bound evidence, not caller-supplied normalized daily rows.
    verified = []
    for batch in batches:
        source = session.get(SourceAsset, batch.asset_id)
        if source is None:
            raise WeatherError("Climate baseline source asset is unavailable.")
        original = batch_from_raw(source.id, SourceAssetInput.model_validate(source))
        if original != batch:
            raise WeatherError("Climate baseline source evidence mismatch.")
        verified.append(original)
    content = baseline_manifest(verified, spec)
    asset_id = baseline_asset_id(content)
    digest = hashlib.sha256(content).hexdigest()
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{digest}.json"
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise WeatherError("Climate manifest checksum mismatch.") from None
    retrieved = max(b.asset.retrieved_at for b in verified)
    asset = SourceAssetInput(
        provider="nasa_power",
        product=BASELINE_PROCESSOR,
        retrieved_at=retrieved,
        storage_path=str(path.resolve()),
        content_hash=digest,
        format="json",
        processing_version=BASELINE_PROCESSOR,
        request_metadata={
            "spec": spec.model_dump(mode="json"),
            "availability": "latest raw-source retrieval; derived deterministic manifest",
        },
    )
    session.execute(
        insert(SourceAsset)
        .values(id=asset_id, **asset.model_dump())
        .on_conflict_do_nothing(index_elements=[SourceAsset.id])
    )
    saved = session.get(SourceAsset, asset_id)
    if saved is None or any(getattr(saved, k) != v for k, v in asset.model_dump().items()):
        raise WeatherError("Climate manifest identity conflict.")
    results = compute_baselines(verified, spec, asset_id)
    for result in results:
        values = result.model_dump(exclude={"schema_version"})
        session.execute(
            insert(ClimateBaselineRecord)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_climate_reference")
        )
        saved_row = session.scalar(
            select(ClimateBaselineRecord).where(
                ClimateBaselineRecord.cell_key == result.cell_key,
                ClimateBaselineRecord.source == result.source,
                ClimateBaselineRecord.reference_start_year == spec.reference_start_year,
                ClimateBaselineRecord.reference_end_year == spec.reference_end_year,
                ClimateBaselineRecord.month == result.month,
                ClimateBaselineRecord.version == spec.version,
            )
        )
        if saved_row is None or ClimateBaseline.model_validate(saved_row) != result:
            raise WeatherError(
                "Baseline version is immutable; choose a new version for changed inputs."
            )
    return results


def build_baselines(
    engine: Engine,
    cells: list[ClimateCell],
    spec: BaselineSpec,
    output_dir: Path,
    *,
    client: PowerClient | None = None,
    offline: bool = False,
    retrieval_cutoff: datetime | None = None,
) -> list[ClimateBaseline]:
    spec = BaselineSpec.model_validate(spec.model_dump())
    unique = {cell.key: ClimateCell.model_validate(cell.model_dump()) for cell in cells}
    if not 1 <= len(unique) <= 64:
        raise ValueError("Build one to sixty-four unique climate cells at a time")
    results = []
    for cell in sorted(unique.values(), key=lambda c: c.key):
        cutoff = utc(retrieval_cutoff or (client.now() if client else datetime.now(UTC)))
        with Session(engine) as session:
            existing = cached_baselines(session, cell, spec, cutoff)
        if existing is not None:
            results.extend(existing)
            continue
        batches = []
        for year in range(spec.reference_start_year, spec.reference_end_year + 1):
            request = PowerRequest(cell=cell, year=year)
            with Session(engine) as session:
                batch = cached_power(session, request, cutoff)
            if batch is None:
                if offline or client is None:
                    raise WeatherError("Required POWER year is unavailable offline.")
                if retrieval_cutoff is not None and cutoff < utc(client.now()):
                    raise WeatherError("Frozen source availability cannot fetch new POWER data.")
                batch = client.fetch(request)
                if retrieval_cutoff is not None and batch.asset.retrieved_at > cutoff:
                    raise WeatherError("POWER data is newer than frozen availability.")
                with Session(engine) as session, session.begin():
                    save_power_batch(session, batch)
            batches.append(batch)
        with Session(engine) as session, session.begin():
            results.extend(publish_baselines(session, batches, spec, output_dir))
    return results


def get_baseline(
    session: Session,
    cell: ClimateCell,
    month: int,
    spec: BaselineSpec,
    *,
    retrieval_cutoff: datetime,
) -> ClimateBaseline | None:
    if not 1 <= month <= 12:
        raise ValueError("Month must be 1 through 12")
    rows = cached_baselines(session, cell, spec, retrieval_cutoff)
    return next((r for r in rows if r.month == month), None) if rows else None
