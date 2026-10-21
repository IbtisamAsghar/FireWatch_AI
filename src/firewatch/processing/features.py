"""features-v1: one finite, past-only feature builder for training/live/replay."""

import math
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

import numpy as np
import yaml
from pydantic import Field, model_validator
from sklearn.neighbors import BallTree  # type: ignore[import-untyped]

from firewatch.processing.baselines import BaselineSpec, climate_context, load_baseline_spec
from firewatch.processing.clusters import (
    ALGORITHM,
    EARTH_RADIUS_KM,
    SnapshotBatch,
    centroid,
    load_clustering_spec,
)
from firewatch.processing.weather import weather_at_cutoff
from firewatch.repositories.revisions import SelectedDetection
from firewatch.schemas.common import DTO, Confidence, DayNight, Digest, RunMode
from firewatch.schemas.contracts import ClimateBaseline, FeatureSet, WeatherSeries
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import VARIABLES, weather_cell
from firewatch.sources.power import climate_cell

Nonnegative = Annotated[float, Field(ge=0, strict=True)]
Fraction = Annotated[float, Field(ge=0, le=1, strict=True)]


class FeatureValues(DTO):
    frp_max_mw: Nonnegative | None
    frp_sum_mw: Nonnegative | None
    brightness_max_k: Annotated[float, Field(gt=0, strict=True)] | None
    detections_6h: Annotated[int, Field(ge=0, strict=True)]
    detections_12h: Annotated[int, Field(ge=0, strict=True)]
    detections_24h: Annotated[int, Field(ge=0, strict=True)]
    hours_since_first_seen_24h: Annotated[float, Field(ge=0, lt=24, strict=True)] | None
    confidence_high_fraction: Fraction | None
    day_fraction: Fraction | None
    temperature_c: Annotated[float, Field(ge=-273.15, strict=True)] | None
    humidity_pct: Annotated[float, Field(ge=0, le=100, strict=True)] | None
    wind_ms: Nonnegative | None
    gust_ms: Nonnegative | None
    precipitation_24h_mm: Nonnegative | None
    temperature_departure_c: Annotated[float, Field(strict=True)] | None
    rainfall_deficit_24h_mm: Nonnegative | None
    weather_age_hours: Nonnegative | None

    @model_validator(mode="after")
    def activity_order(self) -> Self:
        if not self.detections_6h <= self.detections_12h <= self.detections_24h:
            raise ValueError("Activity counts must be nested by lookback")
        if (self.detections_24h == 0) != (self.hours_since_first_seen_24h is None):
            raise ValueError("First-seen age must match observed 24-hour activity")
        return self


FEATURE_NAMES = tuple(FeatureValues.model_fields)
MISSING_NAMES = tuple(name for name in FEATURE_NAMES if not name.startswith("detections_"))
UNITS = dict(
    zip(
        FEATURE_NAMES,
        (
            "MW",
            "MW",
            "K",
            "count",
            "count",
            "count",
            "h",
            "fraction",
            "fraction",
            "C",
            "%",
            "m/s",
            "m/s",
            "mm",
            "C",
            "mm",
            "h",
        ),
        strict=True,
    )
)
MODEL_INPUT_NAMES = FEATURE_NAMES + tuple(name + "_missing" for name in MISSING_NAMES)


class FeatureSpec(DTO):
    feature_version: Literal["features-v1"] = "features-v1"
    replay_weather_product: Literal["historical_forecast", "reanalysis"] = "historical_forecast"
    replay_weather_model: Literal["gfs_seamless", "era5"] = "gfs_seamless"
    weather_namespace: str = Field(default="weather-v1", pattern=r"^[A-Za-z0-9_-]{1,40}$")
    weather_max_age_hours: float = Field(default=3, gt=0, le=168)
    climate: BaselineSpec = Field(default_factory=BaselineSpec)
    max_weather_cells: int = Field(default=256, ge=1, le=1024)

    @model_validator(mode="after")
    def products(self) -> Self:
        if (self.replay_weather_product == "reanalysis") != (self.replay_weather_model == "era5"):
            raise ValueError("Historical forecast uses GFS; explicit reanalysis uses ERA5")
        return self

    def weather_identity(self, mode: RunMode) -> tuple[str, str]:
        return (
            ("live", "gfs_seamless")
            if mode == RunMode.LIVE
            else (self.replay_weather_product, self.replay_weather_model)
        )


def load_feature_spec(path: Path) -> FeatureSpec:
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("Feature configuration exceeds bound")
    config = yaml.safe_load(raw)
    if not isinstance(config, dict) or config.get("schema_version") != "features-v1":
        raise ValueError("Expected features-v1 configuration")
    if (
        config.get("missing_value_policy") != "null_with_quality_flags"
        or config.get("weather_cache_grid_degrees") != 0.1
    ):
        raise ValueError("features-v1 requires the versioned grid and missing-value policy")
    clustering = load_clustering_spec(path)
    if clustering.algorithm_version != ALGORITHM or clustering.radius_km != 2:
        raise ValueError("features-v1 requires the frozen 2 km clustering definition")
    return FeatureSpec(
        weather_max_age_hours=config["weather_max_age_hours"],
        replay_weather_product=config["feature_weather_product"],
        replay_weather_model=config["feature_weather_model"],
        weather_namespace=config["feature_weather_namespace"],
        climate=load_baseline_spec(path),
    )


class AssetPin(DTO):
    asset_id: UUID
    sha256: Digest


class SatelliteAssetPin(AssetPin):
    batch_id: UUID


class FeatureEnrichment(DTO):
    snapshot_id: UUID
    weather: WeatherSeries = Field(default_factory=lambda: WeatherSeries(records=[]))
    weather_assets: list[AssetPin] = Field(default_factory=list)
    baseline: ClimateBaseline | None = None
    baseline_asset: AssetPin | None = None


def validate_feature_set(feature: FeatureSet) -> FeatureSet:
    feature = FeatureSet.model_validate(feature.model_dump())
    if feature.feature_version != "features-v1" or set(feature.values) != set(FEATURE_NAMES):
        raise ValueError("Feature version or input whitelist mismatch")
    FeatureValues.model_validate(feature.values)
    expected = {name: feature.values[name] is None for name in MISSING_NAMES}
    if feature.missing_flags != expected or feature.units != UNITS:
        raise ValueError("Feature missing indicators or units mismatch")
    if digest(feature.model_dump(mode="json", exclude={"feature_hash"})) != feature.feature_hash:
        raise ValueError("Feature payload failed integrity validation")
    return feature


def model_inputs(feature: FeatureSet) -> dict[str, float | int | bool | None]:
    feature = validate_feature_set(feature)
    values = FeatureValues.model_validate(feature.values).model_dump()
    return dict(values) | {name + "_missing": feature.missing_flags[name] for name in MISSING_NAMES}


def build_features(
    snapshots: SnapshotBatch,
    selected: list[SelectedDetection],
    enrichments: list[FeatureEnrichment],
    satellite_assets: list[SatelliteAssetPin],
    config: FeatureSpec | None = None,
) -> list[FeatureSet]:
    """No database, network, clock, imputation, labels or scoring is used here."""
    config = FeatureSpec.model_validate((config or FeatureSpec()).model_dump())
    snapshots = SnapshotBatch.model_validate(snapshots.model_dump())
    if snapshots.config.algorithm_version != ALGORITHM or snapshots.config.radius_km != 2:
        raise ValueError("features-v1 requires the frozen 2 km clustering definition")
    if snapshots.feature_version != config.feature_version:
        raise ValueError("Snapshot and feature schema versions disagree")
    if len({d.snapshot.snapshot_id for d in snapshots.snapshots}) != len(snapshots.snapshots):
        raise ValueError("Snapshot identities must be unique")
    snapshots.snapshots.sort(key=lambda d: d.snapshot.snapshot_id.hex)
    cutoff = snapshots.as_of
    history = [
        SelectedDetection.model_validate(r.model_dump())
        for r in selected
        if cutoff - timedelta(hours=24) < r.observation.acquired_at <= cutoff
    ]
    history.sort(key=lambda r: (r.observation.acquired_at, r.detection_id.hex))
    if (
        len(history) > snapshots.config.max_history
        or len({r.detection_id for r in history}) != len(history)
        or len({r.physical_key for r in history}) != len(history)
        or digest([r.model_dump(mode="json") for r in history]) != snapshots.input_hash
    ):
        raise ValueError("Features must use exactly the snapshot history revisions")
    for row in history:
        if digest(row.observation.model_dump(mode="json")) != row.revision_hash:
            raise ValueError("Feature observation revision hash mismatch")
    if snapshots.history_count != len(history):
        raise ValueError("Snapshot history count does not match frozen inputs")
    current = {
        r.detection_id: r
        for r in history
        if r.observation.acquired_at > cutoff - timedelta(hours=6)
    }
    if snapshots.candidate_count != len(current):
        raise ValueError("Snapshot candidate count does not match frozen inputs")
    member_ids = [i for d in snapshots.snapshots for i in d.snapshot.member_ids]
    if set(member_ids) != set(current) or len(member_ids) != len(current):
        raise ValueError("Snapshot memberships do not partition current candidates")
    enrichment = {
        e.snapshot_id: FeatureEnrichment.model_validate(e.model_dump()) for e in enrichments
    }
    if len(enrichment) != len(enrichments) or set(enrichment) != {
        d.snapshot.snapshot_id for d in snapshots.snapshots
    }:
        raise ValueError("Declare enrichment, including missing inputs, for every snapshot")
    pins = sorted(
        [SatelliteAssetPin.model_validate(p.model_dump()) for p in satellite_assets],
        key=lambda p: p.batch_id.hex,
    )
    if len({p.batch_id for p in pins}) != len(pins) or not {r.batch_id for r in history} <= {
        p.batch_id for p in pins
    }:
        raise ValueError("Satellite asset provenance is incomplete or duplicated")
    if not snapshots.snapshots:
        return []
    tree = BallTree(
        np.radians(
            np.asarray(
                [(r.observation.latitude, r.observation.longitude) for r in history],
                dtype=np.float64,
            )
        ),
        metric="haversine",
    )
    results = []
    total_neighbors = 0
    for details in snapshots.snapshots:
        snapshot = details.snapshot
        if (snapshot.run_id, snapshot.mode, snapshot.as_of, snapshot.feature_version) != (
            snapshots.run_id,
            snapshots.mode,
            cutoff,
            config.feature_version,
        ):
            raise ValueError("Snapshot context does not match its batch")
        members = [current[i] for i in sorted(snapshot.member_ids, key=lambda i: i.hex)]
        if centroid(members) != (snapshot.latitude, snapshot.longitude):
            raise ValueError("Snapshot centroid does not match selected members")
        near = tree.query_radius(
            np.radians([[snapshot.latitude, snapshot.longitude]]),
            r=snapshots.config.radius_km / EARTH_RADIUS_KM,
        )[0]
        total_neighbors += len(near)
        if total_neighbors > snapshots.config.max_neighbor_links:
            raise ValueError("Feature activity neighborhood bound exceeded")
        activity = {
            f"detections_{h}h": sum(
                history[int(i)].observation.acquired_at > cutoff - timedelta(hours=h) for i in near
            )
            for h in (6, 12, 24)
        }
        if activity != details.activity_counts:
            raise ValueError("Snapshot activity counts do not match frozen observations")
        age = (
            (cutoff - min(history[int(i)].observation.acquired_at for i in near)).total_seconds()
            / 3600
            if len(near)
            else None
        )
        known_frp = [r.observation.frp_mw for r in members if r.observation.frp_mw is not None]
        frp_max = max(known_frp) if known_frp else None
        frp_sum = math.fsum(known_frp) if known_frp else None
        if (snapshot.frp_max_mw, snapshot.frp_sum_mw) != (frp_max, frp_sum):
            raise ValueError("Snapshot FRP does not match its frozen revisions")
        brightness = [
            r.observation.brightness_k for r in members if r.observation.brightness_k is not None
        ]
        confidence = [
            r.observation.confidence
            for r in members
            if r.observation.confidence != Confidence.UNKNOWN
        ]
        daylight = [r.observation.daynight for r in members if r.observation.daynight is not None]
        flags = list(details.quality_flags) + list(snapshots.quality_flags)
        if len(brightness) < len(members):
            flags.append("brightness_missing_members")
        if len(confidence) < len(members):
            flags.append("confidence_unknown_members")
        if len(daylight) < len(members):
            flags.append("daynight_missing_members")
        inputs = enrichment[snapshot.snapshot_id]
        end = cutoff.replace(minute=0, second=0, microsecond=0)
        weather = WeatherSeries(
            records=sorted(
                [
                    r
                    for r in inputs.weather.records
                    if end - timedelta(hours=23) <= r.valid_at <= end
                ],
                key=lambda r: r.valid_at,
            ),
            quality_flags=inputs.weather.quality_flags,
        )
        cell = weather_cell(snapshot.latitude, snapshot.longitude)
        product, model = config.weather_identity(snapshots.mode)
        expected_units = {field: unit for field, unit in VARIABLES.values()}
        for weather_row in weather.records:
            if (
                weather_row.cell_key != cell.key
                or (weather_row.requested_latitude, weather_row.requested_longitude)
                != (cell.latitude, cell.longitude)
                or weather_row.provider != "open_meteo"
                or weather_row.product != product
                or weather_row.model != model
                or weather_row.units != expected_units
            ):
                raise ValueError("Weather cell, product, model or units disagree with features-v1")
        weather_ids = {r.asset_id for r in weather.records}
        weather_assets = sorted(
            [p for p in inputs.weather_assets if p.asset_id in weather_ids],
            key=lambda p: p.asset_id.hex,
        )
        if (
            {p.asset_id for p in weather_assets} != {r.asset_id for r in weather.records}
            or len(weather_assets) != len({p.asset_id for p in weather_assets})
            or len({r.asset_id for r in weather.records}) > 1
        ):
            raise ValueError("Feature weather asset provenance is inconsistent")
        climate = inputs.baseline
        if (climate is None) != (inputs.baseline_asset is None) or (
            climate is not None
            and inputs.baseline_asset is not None
            and inputs.baseline_asset.asset_id != climate.asset_id
        ):
            raise ValueError("Feature baseline asset provenance mismatch")
        if climate is not None and (
            climate.version != config.climate.version
            or climate.reference_start_year != config.climate.reference_start_year
            or climate.reference_end_year != config.climate.reference_end_year
        ):
            raise ValueError("Feature climate reference does not match configuration")
        if climate is not None and (
            climate.statistics.get("min_valid_fraction") != config.climate.min_valid_fraction
            or climate.statistics.get("earliest_snapshot")
            != config.climate.earliest_snapshot.isoformat()
            or climate.statistics.get("units")
            != {"temperature_mean_c": "C", "precipitation_mean_mm": "mm/day"}
        ):
            raise ValueError("Feature climate coverage definition or units mismatch")
        weather_value = weather_at_cutoff(
            weather, cutoff, max_age_hours=config.weather_max_age_hours
        )
        context = climate_context(
            weather,
            cutoff,
            climate_cell(cell.latitude, cell.longitude),
            climate,
            weather_max_age_hours=config.weather_max_age_hours,
        )
        flags += context.quality_flags
        wr = weather_value.record
        values = FeatureValues(
            frp_max_mw=frp_max,
            frp_sum_mw=frp_sum,
            brightness_max_k=max(brightness) if brightness else None,
            **activity,
            hours_since_first_seen_24h=age,
            confidence_high_fraction=confidence.count(Confidence.HIGH) / len(confidence)
            if confidence
            else None,
            day_fraction=daylight.count(DayNight.DAY) / len(members)
            if len(daylight) == len(members)
            else None,
            temperature_c=wr.temperature_c if wr else None,
            humidity_pct=wr.humidity_pct if wr else None,
            wind_ms=wr.wind_ms if wr else None,
            gust_ms=wr.gust_ms if wr else None,
            precipitation_24h_mm=weather_value.precipitation_24h_mm,
            temperature_departure_c=context.temperature_departure_c,
            rainfall_deficit_24h_mm=context.rainfall_deficit_24h_mm,
            weather_age_hours=weather_value.weather_age_hours,
        ).model_dump(mode="json")
        source_ids = {str(p.asset_id) for p in pins + weather_assets}
        if inputs.baseline_asset:
            source_ids.add(str(inputs.baseline_asset.asset_id))
        lineage = {
            "processor": config.feature_version,
            "config": config.model_dump(mode="json"),
            "snapshot_input_hash": snapshots.input_hash,
            "snapshot_algorithm_version": snapshot.algorithm_version,
            "satellite_assets": [p.model_dump(mode="json") for p in pins],
            "member_revisions": [
                dict(
                    detection_id=str(r.detection_id),
                    batch_id=str(r.batch_id),
                    revision_hash=r.revision_hash,
                )
                for r in members
            ],
            "source_asset_ids": sorted(source_ids),
            "weather": dict(
                cell_key=cell.key,
                product=product,
                model=model,
                assets=[p.model_dump(mode="json") for p in weather_assets],
                records_sha256=digest(weather.model_dump(mode="json")),
                valid_at=wr.valid_at.isoformat() if wr else None,
                source_version=wr.source_version if wr else None,
                precipitation_interval_end=weather_value.precipitation_interval_end.isoformat(),
            ),
            "baseline": dict(
                asset=inputs.baseline_asset.model_dump(mode="json"),
                version=climate.version,
                cell_key=climate.cell_key,
                month=climate.month,
                reference_years=[climate.reference_start_year, climate.reference_end_year],
                value_sha256=digest(climate.model_dump(mode="json")),
            )
            if climate and inputs.baseline_asset
            else None,
            "quality_flags": sorted(set(flags)),
            "valid_member_counts": dict(
                frp=len(known_frp),
                brightness=len(brightness),
                confidence=len(confidence),
                daynight=len(daylight),
            ),
        }
        result = FeatureSet(
            run_id=snapshots.run_id,
            mode=snapshots.mode,
            as_of=cutoff,
            feature_version=config.feature_version,
            snapshot_id=snapshot.snapshot_id,
            values=values,
            missing_flags={n: values[n] is None for n in MISSING_NAMES},
            units=dict(UNITS),
            lineage=lineage,
            feature_hash="0" * 64,
        )
        result.feature_hash = digest(result.model_dump(mode="json", exclude={"feature_hash"}))
        results.append(validate_feature_set(result))
    return results
