"""Restartable staged pipeline. Provider I/O and scoring run outside DB transactions."""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.models import (
    Alert,
    AnchorOutbox,
    IngestionBatch,
    Region,
    Run,
    Score,
    SourceAsset,
)
from firewatch.evidence.canonical import canonical_bytes, evidence_hash
from firewatch.ml.predict import score_features
from firewatch.pipeline.jobs import Lease, fence
from firewatch.processing.clusters import ClusteringSpec
from firewatch.processing.features import FeatureSpec, load_feature_spec
from firewatch.repositories.features import build_run_features
from firewatch.repositories.ingestion import persist_normalized
from firewatch.repositories.revisions import RevisionPolicy
from firewatch.repositories.runs import complete_and_publish_run
from firewatch.repositories.snapshots import build_run_snapshots
from firewatch.repositories.weather import cached_weather, save_weather
from firewatch.schemas.common import (
    DTO,
    Completeness,
    JobType,
    Priority,
    Product,
    RunMode,
    RunStatus,
    Sensor,
    Stage,
    UTCDateTime,
)
from firewatch.schemas.contracts import BoundingBox, FeatureSet
from firewatch.schemas.firms_refresh import RefreshSpec
from firewatch.sources.firms import FirmsClient
from firewatch.sources.firms_normalize import digest, normalize_firms_batch
from firewatch.sources.open_meteo import OpenMeteoClient, WeatherRequest, weather_cell
from firewatch.sources.weather_budget import WeatherError


class PipelineRequest(DTO):
    schema_version: Literal["jobs-v1"]
    region_id: UUID
    mode: RunMode
    as_of: UTCDateTime
    window_start: UTCDateTime
    window_end: UTCDateTime
    scorer_version: str
    feature_version: str
    asset_ids: list[UUID] = Field(default_factory=list, max_length=200)
    scenario_id: str | None = None
    scenario_name: str | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "PipelineRequest":
        if self.window_start >= self.window_end or self.window_end != self.as_of:
            raise ValueError("Invalid pipeline window")
        if self.mode == RunMode.REPLAY and not self.asset_ids:
            raise ValueError("Replay requires explicitly pinned assets")
        if self.feature_version != "features-v1" or self.scorer_version != "baseline-v1":
            raise ValueError("Unsupported scorer or feature version")
        return self


def _quality_flags(feature: FeatureSet) -> list[str]:
    flags = feature.lineage.get("quality_flags", [])
    return [f for f in flags if isinstance(f, str)] if isinstance(flags, list) else []


def _checkpoint(run: Run, stage: Stage) -> None:
    run.stage_status = dict(run.stage_status) | {stage.value: "succeeded"}


def _run(session: Session, identifier: UUID) -> Run:
    run = session.scalar(select(Run).where(Run.id == identifier).with_for_update())
    if run is None:
        raise ValueError("Pipeline run unavailable")
    return run


def execute_run(
    engine: Engine,
    settings: Settings,
    lease: Lease,
    now: Callable[[], datetime],
    *,
    firms: FirmsClient | None = None,
    weather: OpenMeteoClient | None = None,
    after_stage: Callable[[Stage], None] = lambda _: None,
) -> UUID:
    """Each checkpoint commits independently; every output commit fences lease ownership."""
    with Session(engine) as session, session.begin():
        job = fence(session, lease, now())
        if job.type not in {JobType.REFRESH, JobType.REPLAY}:
            raise ValueError("Unsupported pipeline job type")
        request = PipelineRequest.model_validate(job.payload)
        if (job.type == JobType.REFRESH) != (request.mode == RunMode.LIVE):
            raise ValueError("Job type and run mode disagree")
        if request.as_of > now():
            raise ValueError("Pipeline cutoff cannot be in the future")
        area = session.get(Region, request.region_id)
        if area is None or not area.enabled:
            raise ValueError("Pipeline requires an enabled region")
        if (area.east - area.west) * (area.north - area.south) > settings.max_region_area_degrees2:
            raise ValueError("Region exceeds configured bound")
        bbox = BoundingBox(west=area.west, south=area.south, east=area.east, north=area.north)
        region_name = area.name
        key = f"pipeline-v1:{area.id}:{request.mode}:{request.as_of.isoformat()}"
        identifier = uuid5(NAMESPACE_URL, key)
        # All workers first lock the job then the run, including duplicate requests.
        run = session.get(Run, identifier)
        if run is None:
            config = load_feature_spec(settings.features_config)
            if (
                settings.cluster_radius_km != 2
                or settings.snapshot_lookback_hours != 6
                or settings.weather_cache_grid_degrees != 0.1
            ):
                raise ValueError("features-v1 requires the frozen cluster/grid definition")
            config.weather_max_age_hours = settings.weather_max_age_hours
            frozen: dict[str, Any] = dict(
                request=request.model_dump(mode="json"),
                bbox=bbox.model_dump(),
                features=config.model_dump(mode="json"),
                evidence=settings.evidence_schema_version,
                detection_max_age_hours=settings.detection_max_age_hours,
                network=f"eip155:{settings.chain_id}:{settings.alert_registry_address.lower()}"
                if settings.chain_enabled
                else None,
            )
            if frozen["evidence"] != "evidence-v1":
                raise ValueError("Unsupported evidence schema")
            run = Run(
                id=identifier,
                region_id=area.id,
                mode=request.mode,
                as_of=request.as_of,
                run_key=key,
                config_hash=digest(frozen),
                status=RunStatus.RUNNING,
                scorer_version=request.scorer_version,
                feature_version=request.feature_version,
                started_at=now(),
                stage_status={},
                completeness=Completeness.UNAVAILABLE,
                input_asset_manifest={"orchestration": frozen},
            )
            session.add(run)
            session.flush()
        else:
            run = _run(session, identifier)
            frozen = run.input_asset_manifest["orchestration"]
            if (
                digest(frozen) != run.config_hash
                or frozen["bbox"] != bbox.model_dump()
                or frozen["request"] != request.model_dump(mode="json")
            ):
                raise ValueError("Run key conflicts with changed inputs or configuration")
            config = FeatureSpec.model_validate(frozen["features"])
            if run.status == RunStatus.FAILED:
                run.status = RunStatus.RUNNING
        job.result_run_id = identifier
        if run.status == RunStatus.COMPLETED:
            return identifier

    # Ingest one provider chunk at a time, retaining completed chunks after a crash.
    if request.mode == RunMode.LIVE:
        spec = RefreshSpec(region=region_name, as_of=request.as_of)
        for index, source_request in enumerate(spec.requests(bbox)):
            with Session(engine) as session:
                run = session.get(Run, identifier)
                assert run is not None
                done = run.input_asset_manifest.get("ingest_chunks", {})
                if str(index) in done:
                    continue
            if firms is None:
                raise ValueError("FIRMS client is required for pending live ingestion")
            if now() - request.as_of > timedelta(hours=settings.max_refresh_age_hours):
                raise ValueError("Saved live cutoff is too old to fetch")
            # A fresh fetch ID avoids concurrent stale-attempt filesystem collisions.
            raw = firms.fetch(source_request, batch_id=uuid4())
            normalized = normalize_firms_batch(raw)
            with Session(engine) as session, session.begin():
                fence(session, lease, now())
                run = _run(session, identifier)
                ingested = persist_normalized(session, normalized, request.region_id)
                chunks = dict(run.input_asset_manifest.get("ingest_chunks", {}))
                chunks[str(index)] = str(ingested.batch_id)
                run.input_asset_manifest = dict(run.input_asset_manifest) | {
                    "ingest_chunks": chunks
                }

    with Session(engine) as session, session.begin():
        fence(session, lease, now())
        run = _run(session, identifier)
        if run.stage_status.get(Stage.INGEST.value) != "succeeded":
            if request.mode == RunMode.LIVE:
                ids = list(run.input_asset_manifest["ingest_chunks"].values())
                products = {Sensor.NOAA20: Product.NOAA20_NRT, Sensor.NOAA21: Product.NOAA21_NRT}
            else:
                batches = list(
                    session.scalars(
                        select(IngestionBatch).where(
                            IngestionBatch.region_id == request.region_id,
                            IngestionBatch.asset_id.in_(request.asset_ids),
                        )
                    )
                )
                if {b.asset_id for b in batches} != set(request.asset_ids):
                    raise ValueError("Pinned replay satellite assets are unavailable")
                ids = [str(b.id) for b in batches]
                products = {}
                for b in batches:
                    sensor = Sensor.NOAA20 if "NOAA20" in b.product else Sensor.NOAA21
                    if sensor in products and products[sensor] != b.product:
                        raise ValueError("Replay mixes different products for one sensor")
                    products[sensor] = b.product
            policy = RevisionPolicy(
                products=products,
                batch_ids=ids,
                retrieval_cutoff=now(),
                allow_rejected_batches=True,
            )
            chosen = list(
                session.scalars(
                    select(IngestionBatch).where(IngestionBatch.id.in_(policy.batch_ids))
                )
            )
            if any(
                b.fetched_count > 0 and b.accepted_count + b.duplicate_count == 0 for b in chosen
            ):
                raise ValueError("A satellite source contains only rejected rows")
            # Validate complete source days before claiming ingest success.
            from firewatch.repositories.snapshots import check_coverage

            check_coverage(session, run, policy)
            run.input_asset_manifest = dict(run.input_asset_manifest) | {
                "ingestion_policy": policy.model_dump(mode="json")
            }
            _checkpoint(run, Stage.INGEST)
    after_stage(Stage.INGEST)

    with Session(engine) as session, session.begin():
        fence(session, lease, now())
        run = _run(session, identifier)
        policy = RevisionPolicy.model_validate(run.input_asset_manifest["ingestion_policy"])
        snapshots = build_run_snapshots(session, identifier, policy, ClusteringSpec())
    after_stage(Stage.SNAPSHOTS)

    # Climate is precomputed. Live weather fetches only bounded missing cells, without locks.
    with Session(engine) as session:
        run = session.get(Run, identifier)
        assert run is not None
        need_enrichment = run.stage_status.get(Stage.ENRICHMENT.value) != "succeeded"
        weather_warning = "weather_source_unavailable" in run.input_asset_manifest.get(
            "coverage_warnings", []
        )
    if need_enrichment and request.mode == RunMode.LIVE:
        cells = {
            weather_cell(d.snapshot.latitude, d.snapshot.longitude).key: weather_cell(
                d.snapshot.latitude, d.snapshot.longitude
            )
            for d in snapshots.snapshots
        }
        if len(cells) > config.max_weather_cells:
            raise ValueError("Weather cell bound exceeded")
        end = request.as_of.replace(minute=0, second=0, microsecond=0)
        for cell in cells.values():
            wr = WeatherRequest(
                cells=[cell],
                start=end - timedelta(hours=23),
                end=end,
                product="live",
                model="gfs_seamless",
                mode=RunMode.LIVE,
                source_version=config.weather_namespace,
            )
            with Session(engine) as session:
                cached = cached_weather(
                    session,
                    wr,
                    now(),
                    oldest_retrieval=now() - timedelta(hours=config.weather_max_age_hours),
                )
            if cached is not None:
                continue
            try:
                if weather is None:
                    raise WeatherError("Live weather client unavailable")
                batch = weather.fetch(wr)
                with Session(engine) as session, session.begin():
                    fence(session, lease, now())
                    save_weather(session, batch)
            except WeatherError:
                weather_warning = True  # Missing inputs remain null and quality flagged.

    with Session(engine) as session, session.begin():
        fence(session, lease, now())
        run = _run(session, identifier)
        availability = None if "features" in run.input_asset_manifest else now()
        features = build_run_features(session, identifier, config, retrieval_cutoff=availability)
        run = _run(session, identifier)
        quality = set(snapshots.quality_flags) | {
            flag for f in features for flag in _quality_flags(f)
        }
        informational = {
            "precipitation_24h_ends_at_completed_hour",
            "hourly_model_values",
            "precipitation_preceding_hour",
            "provider_interpolation_unknown",
            "retrospective_weather",
        }
        warnings = sorted(
            (quality - informational)
            | {
                name + "_missing"
                for f in features
                for name, missing in f.missing_flags.items()
                if missing
            }
        )
        if weather_warning:
            warnings = sorted(set(warnings) | {"weather_source_unavailable"})
        latest_detection = max(
            (d.snapshot.latest_detection_at for d in snapshots.snapshots), default=None
        )
        if latest_detection and (request.as_of - latest_detection).total_seconds() / 3600 > float(
            frozen["detection_max_age_hours"]
        ):
            warnings = sorted(set(warnings) | {"satellite_observations_stale"})
        pins = run.input_asset_manifest["features"]["pins"]
        from firewatch.processing.features import FeatureValues

        valid = [
            request.as_of - timedelta(hours=age)
            for f in features
            if (age := FeatureValues.model_validate(f.values).weather_age_hours) is not None
        ]
        source_ids = {UUID(p["asset_id"]) for p in pins["assets"] + pins["satellite_assets"]}
        retrieved = list(
            session.scalars(select(SourceAsset.retrieved_at).where(SourceAsset.id.in_(source_ids)))
        )
        run.completeness = Completeness.PARTIAL if warnings else Completeness.COMPLETE
        run.input_asset_manifest = dict(run.input_asset_manifest) | {
            "coverage_warnings": warnings,
            "quality_flags": sorted(quality),
            "freshness": {
                "latest_weather_valid_at": max(valid).isoformat() if valid else None,
                "latest_retrieved_at": max(retrieved).isoformat() if retrieved else None,
            },
        }
    after_stage(Stage.ENRICHMENT)
    after_stage(Stage.FEATURES)

    # Model/scorer computation is deliberately outside any DB transaction or lock.
    with Session(engine) as session:
        run = session.get(Run, identifier)
        assert run is not None
        scoring_done = run.stage_status.get(Stage.SCORING.value) == "succeeded"
    if not scoring_done:
        scores = score_features(features, request.scorer_version, scored_at=now())
        with Session(engine) as session, session.begin():
            fence(session, lease, now())
            run = _run(session, identifier)
            for result in scores:
                session.add(
                    Score(
                        id=uuid5(identifier, "score:" + str(result.snapshot_id)),
                        snapshot_id=result.snapshot_id,
                        run_id=identifier,
                        scorer_version=result.scorer_version,
                        model_id=None,
                        priority_score=result.priority_score,
                        score_kind=result.score_kind,
                        escalation_probability=None,
                        priority=result.priority,
                        explanation=result.explanation,
                        scored_at=result.scored_at,
                    )
                )
            _checkpoint(run, Stage.SCORING)
    after_stage(Stage.SCORING)

    with Session(engine) as session, session.begin():
        fence(session, lease, now())
        run = _run(session, identifier)
        if run.stage_status.get(Stage.EVIDENCE.value) != "succeeded":
            for score in session.scalars(
                select(Score).where(Score.run_id == identifier, Score.priority == Priority.CRITICAL)
            ):
                feature = next(f for f in features if f.snapshot_id == score.snapshot_id)
                alert_id = uuid5(score.id, "evidence-v1")
                payload = dict(
                    schema_version="evidence-v1",
                    alert_id=str(alert_id),
                    run_id=str(identifier),
                    mode=request.mode.value,
                    as_of=request.as_of.isoformat(),
                    snapshot_id=str(score.snapshot_id),
                    score_id=str(score.id),
                    scorer_version=score.scorer_version,
                    priority_score=score.priority_score,
                    priority=score.priority.value,
                    score_kind=score.score_kind.value,
                    escalation_probability=None,
                    scored_at=score.scored_at.isoformat(),
                    explanation=score.explanation,
                    feature=feature.model_dump(mode="json"),
                    source_manifest={
                        "satellite": run.input_asset_manifest["firms"],
                        "enrichment": run.input_asset_manifest["features"]["pins"],
                    },
                )
                content = canonical_bytes(payload)
                session.add(
                    Alert(
                        id=alert_id,
                        snapshot_id=score.snapshot_id,
                        run_id=identifier,
                        score_id=score.id,
                        canonical_payload=content,
                        original_hash=evidence_hash(content),
                        evidence_schema_version="evidence-v1",
                        created_at=now(),
                    )
                )
                session.flush()
                if frozen["network"]:
                    session.add(
                        AnchorOutbox(
                            id=uuid5(alert_id, frozen["network"]),
                            alert_id=alert_id,
                            network=frozen["network"],
                            available_at=now(),
                        )
                    )
            _checkpoint(run, Stage.EVIDENCE)
    after_stage(Stage.EVIDENCE)

    with Session(engine) as session, session.begin():
        fence(session, lease, now())
        complete_and_publish_run(session, identifier, now())
    return identifier
