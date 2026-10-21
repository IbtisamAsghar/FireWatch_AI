"""Synthetic satellite/weather/climate inputs for features-v1 acceptance."""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from cluster_factory import RUN, T, selected
from weather_factory import NOW, payload

from firewatch.processing.clusters import build_snapshots
from firewatch.processing.features import AssetPin, FeatureEnrichment, SatelliteAssetPin
from firewatch.schemas.common import DayNight, RunMode
from firewatch.schemas.contracts import ClimateBaseline
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import WeatherRequest, parse_weather, weather_cell
from firewatch.sources.power import climate_cell


def observation(i, *, daynight=DayNight.DAY, brightness=330.0, **kw):
    row = selected(i, **kw)
    row.observation.daynight = daynight
    row.observation.brightness_k = brightness
    row.revision_hash = digest(row.observation.model_dump(mode="json"))
    return row


def inputs(rows=None, *, mode=RunMode.REPLAY, cutoff=T, baseline=True):
    rows = rows if rows is not None else [observation(1), observation(2, hours=20)]
    batch = build_snapshots(RUN, mode, cutoff, rows)
    enrichment = []
    for details in batch.snapshots:
        snapshot = details.snapshot
        cell = weather_cell(snapshot.latitude, snapshot.longitude)
        end = cutoff.replace(minute=0, second=0, microsecond=0)
        request = WeatherRequest(
            cells=[cell],
            start=end - timedelta(hours=23),
            end=end,
            mode=mode,
            product="live" if mode == RunMode.LIVE else "historical_forecast",
        )
        asset_id = uuid5(NAMESPACE_URL, "synthetic-weather:" + cell.key)
        weather = parse_weather(
            payload(request), request, NOW, asset_id, "weather-v1-" + asset_id.hex
        )
        cc = climate_cell(cell.latitude, cell.longitude)
        baseline_id = uuid5(NAMESPACE_URL, "synthetic-climate:" + cc.key)
        climate = (
            ClimateBaseline(
                cell_key=cc.key,
                latitude=cc.latitude,
                longitude=cc.longitude,
                reference_start_year=2010,
                reference_end_year=2019,
                month=cutoff.month,
                temperature_mean_c=10.0,
                precipitation_mean_mm=2.0,
                sample_counts={"temperature_c": 310},
                statistics={
                    "processor": "power-monthly-v1",
                    "time_standard": "UTC",
                    "quality_flags": [],
                    "min_valid_fraction": 0.9,
                    "earliest_snapshot": "2022-05-01T00:00:00+00:00",
                    "units": {"temperature_mean_c": "C", "precipitation_mean_mm": "mm/day"},
                },
                version="power-monthly-v1",
                asset_id=baseline_id,
            )
            if baseline
            else None
        )
        enrichment.append(
            FeatureEnrichment(
                snapshot_id=snapshot.snapshot_id,
                weather=weather,
                weather_assets=[AssetPin(asset_id=asset_id, sha256="b" * 64)],
                baseline=climate,
                baseline_asset=AssetPin(asset_id=baseline_id, sha256="c" * 64)
                if baseline
                else None,
            )
        )
    satellite = [
        SatelliteAssetPin(
            batch_id=identifier, asset_id=uuid5(NAMESPACE_URL, str(identifier)), sha256="a" * 64
        )
        for identifier in sorted({r.batch_id for r in rows}, key=str)
    ]
    return batch, rows, enrichment, satellite
