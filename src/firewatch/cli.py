"""Operator entrypoint; --help/--version work before credentials are configured."""

import json
import sqlite3
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
import yaml
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from firewatch.config import ConfigurationError, get_settings
from firewatch.db.session import Database

app = typer.Typer(help="FireWatch AI backend operators' CLI.", no_args_is_help=True)


@app.command("check-config")
def check_config() -> None:
    """Validate configuration without connecting or displaying secret values."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    typer.echo(
        json.dumps(
            {
                "status": "valid",
                "environment": settings.app_env,
                "scorer": settings.active_scorer,
                "firms_configured": settings.firms_map_key is not None,
                "chain_enabled": settings.chain_enabled,
            }
        )
    )


@app.command("db-check")
def db_check() -> None:
    """Check PostgreSQL connectivity, role, database, server version and UTC session."""
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    database = Database(settings)
    try:
        typer.echo(json.dumps({"status": "ok", **database.check_connection()}))
    except SQLAlchemyError:
        typer.echo("Database unavailable. Check the local server and DATABASE_URL.", err=True)
        raise typer.Exit(1) from None
    finally:
        database.close()


def version_callback(value: bool) -> None:
    if value:
        typer.echo(version("firewatch-ai"))
        raise typer.Exit()


@app.callback()
def main(
    show_version: bool = typer.Option(
        False, "--version", callback=version_callback, is_eager=True, help="Print package version."
    ),
) -> None:
    """Inspect configuration and bootstrap database connectivity."""


@app.command("seed-regions")
def seed_regions_command(
    config: Annotated[
        Path, typer.Option("--config", help="Validated regions-v1 YAML configuration.")
    ] = Path("configs/regions.yaml"),
) -> None:
    """Idempotently create/update regions without enabling unvalidated source coverage."""
    from firewatch.repositories.regions import load_regions, seed_regions

    try:
        configuration = load_regions(config)
        settings = get_settings()
    except (OSError, ValueError, ValidationError, yaml.YAMLError, ConfigurationError):
        typer.echo(
            "Invalid region configuration or environment. Check the config path, bbox and secrets.",
            err=True,
        )
        raise typer.Exit(2) from None
    database = Database(settings)
    try:
        with database.transaction() as session:
            identifiers = seed_regions(session, configuration)
        typer.echo(
            json.dumps(
                {
                    "status": "ok",
                    "regions": len(identifiers),
                    "region_ids": [str(identifier) for identifier in identifiers],
                }
            )
        )
    except SQLAlchemyError:
        typer.echo(
            "Region seed failed. Check database connectivity and run alembic upgrade head.",
            err=True,
        )
        raise typer.Exit(1) from None
    finally:
        database.close()


@app.command("probe-sources")
def probe_sources_command(
    region: Annotated[str, typer.Option("--region")] = "northern-california",
    config: Annotated[Path, typer.Option("--config")] = Path("configs/regions.yaml"),
    output: Annotated[Path, typer.Option("--output")] = Path("data/raw/source-access"),
    weather_only: Annotated[bool, typer.Option("--weather-only")] = False,
) -> None:
    """Prove source access with bounded samples; save private raw bytes and a safe report."""
    import httpx

    from firewatch.logging import install_redaction
    from firewatch.repositories.regions import load_regions
    from firewatch.sources.access import AccessError, new_output, probe_sources

    def close() -> None:
        pass

    try:
        settings = get_settings()
        close = install_redaction(settings)
        regions = load_regions(config)
        selected = next((item for item in regions.regions if item.name == region), None)
        if selected is None:
            raise AccessError("Unknown configured region.")
        destination = new_output(output)
        typer.echo(f"Source access evidence: {destination}")
        with httpx.Client(
            timeout=httpx.Timeout(20, connect=5, write=5, pool=5),
            follow_redirects=False,
            headers={"User-Agent": "FireWatch-AI/0.1 source-access-study"},
        ) as client:
            report = probe_sources(
                selected, destination, settings.firms_map_key, client, weather_only
            )
        typer.echo(
            f"Probe status: {report.status}; review report.json before changing region status."
        )
        if report.status == "blocked":
            raise typer.Exit(1)
    except (AccessError, ConfigurationError, OSError, ValueError, yaml.YAMLError) as exc:
        message = (
            str(exc)
            if isinstance(exc, AccessError | ConfigurationError)
            else "Invalid configuration or inaccessible local source/output path."
        )
        typer.echo(message, err=True)
        raise typer.Exit(1) from None
    finally:
        close()


@app.command("inspect-firms-sample")
def inspect_firms_sample_command(
    path: Annotated[Path, typer.Option("--path", exists=True, dir_okay=False)],
    start: Annotated[str, typer.Option("--start", help="UTC date YYYY-MM-DD")],
    product: Annotated[str, typer.Option("--product")],
    days: Annotated[int, typer.Option("--days", min=1, max=5)] = 1,
    region: Annotated[str, typer.Option("--region")] = "northern-california",
    config: Annotated[Path, typer.Option("--config")] = Path("configs/regions.yaml"),
) -> None:
    """Inspect a small operator-supplied archive CSV without importing or changing it."""
    from datetime import date

    from firewatch.repositories.regions import load_regions
    from firewatch.schemas.common import Product
    from firewatch.sources.access import AccessError, archive_inspection

    try:
        selected = next(
            (item for item in load_regions(config).regions if item.name == region), None
        )
        if selected is None:
            raise AccessError("Unknown configured region.")
        result = archive_inspection(
            path, Product(product), selected, date.fromisoformat(start), days
        )
        typer.echo(json.dumps(result, indent=2, ensure_ascii=False))
    except (AccessError, OSError, ValueError, yaml.YAMLError) as exc:
        typer.echo(
            str(exc)
            if isinstance(exc, AccessError)
            else "Invalid product, date, configuration or local CSV path.",
            err=True,
        )
        raise typer.Exit(1) from None


@app.command("fetch-firms")
def fetch_firms_command(
    start: Annotated[str, typer.Option("--start", help="First UTC calendar day, YYYY-MM-DD")],
    product: Annotated[str, typer.Option("--product", help="Allowlisted NOAA-20/21 NRT/SP ID")],
    days: Annotated[int, typer.Option("--days", min=1, max=5)] = 1,
    region: Annotated[str, typer.Option("--region")] = "northern-california",
    config: Annotated[Path, typer.Option("--config")] = Path("configs/regions.yaml"),
    output: Annotated[Path | None, typer.Option("--output")] = None,
) -> None:
    """Fetch bounded FIRMS raw CSV and metadata; no normalization or database writes."""
    from firewatch.logging import install_redaction
    from firewatch.repositories.regions import load_regions
    from firewatch.sources.firms import FirmsAreaRequest, FirmsClient, FirmsError

    def close() -> None:
        pass

    try:
        settings = get_settings()
        close = install_redaction(settings)
        if settings.firms_map_key is None:
            typer.echo("FIRMS_MAP_KEY is missing; configure the local secret.", err=True)
            raise typer.Exit(1)
        selected = next(
            (item for item in load_regions(config).regions if item.name == region), None
        )
        if selected is None:
            typer.echo("Unknown configured region.", err=True)
            raise typer.Exit(1)
        request = FirmsAreaRequest.model_validate(
            {"product": product, "bbox": selected.bbox, "start_date": start, "days": days}
        )
        with FirmsClient(
            settings.firms_map_key,
            output_dir=output if output is not None else settings.raw_data_dir / "firms",
        ) as provider:
            batch = provider.fetch(request)
        typer.echo(
            json.dumps(
                {
                    "batch_id": str(batch.batch_id),
                    "product": batch.request.product.value,
                    "row_count": batch.row_count,
                    "empty": batch.empty,
                    "retrieved_at": batch.retrieved_at.isoformat(),
                    "raw_path": batch.asset.storage_path,
                    "sha256": batch.asset.content_hash,
                    "manifest_path": str(batch.manifest_path),
                    "normalized": False,
                },
                indent=2,
            )
        )
    except FirmsError as exc:
        typer.echo(f"{exc.code}: {exc}", err=True)
        if exc.manifest_path is not None:
            typer.echo(f"Evidence manifest: {exc.manifest_path}", err=True)
        if exc.retry_after_seconds is not None:
            typer.echo(f"Defer for at least {exc.retry_after_seconds:.0f} seconds.", err=True)
        raise typer.Exit(1) from None
    except ConfigurationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    except (ValueError, OSError, yaml.YAMLError):
        typer.echo(
            "Invalid FIRMS product, region, date window or local output configuration.", err=True
        )
        raise typer.Exit(1) from None
    finally:
        close()


@app.command("normalize-firms")
def normalize_firms_command(
    manifest: Annotated[Path, typer.Option("--manifest")],
    invalid_frp: Annotated[str, typer.Option("--invalid-frp")] = "reject",
    missing_frp: Annotated[str, typer.Option("--missing-frp")] = "allow",
    persist: Annotated[bool, typer.Option("--persist")] = False,
    region: Annotated[str | None, typer.Option("--region")] = None,
) -> None:
    """Normalize saved bounded CSV; optionally upsert into an existing region."""
    from sqlalchemy import select

    from firewatch.db.models import Region
    from firewatch.logging import install_redaction
    from firewatch.repositories.ingestion import persist_normalized
    from firewatch.sources.firms import FirmsError
    from firewatch.sources.firms_normalize import (
        NormalizationPolicy,
        load_saved_firms_batch,
        normalize_firms_batch,
        write_quality_reports,
    )

    def close() -> None:
        pass

    database = None
    try:
        if persist and region is None:
            raise ValueError("Persistence requires an explicit region")
        if region is not None and not persist:
            raise ValueError("Region selection requires persistence")
        normalized = normalize_firms_batch(
            load_saved_firms_batch(manifest),
            NormalizationPolicy.model_validate(
                {"invalid_frp": invalid_frp, "missing_frp": missing_frp}
            ),
        )
        summary, rejected = write_quality_reports(normalized, manifest.resolve().parent)
        result = {
            "quality": normalized.report.model_dump(mode="json", exclude={"rejection_samples"}),
            "quality_report": str(summary),
            "rejected_rows": str(rejected),
            "persisted": False,
        }
        if persist:
            settings = get_settings()
            close = install_redaction(settings)
            database = Database(settings)
            with database.transaction() as session:
                area = session.scalar(select(Region).where(Region.name == region))
                if area is None:
                    raise ValueError("Unknown region")
                saved = persist_normalized(session, normalized, area.id)
                result["ingestion"] = saved.model_dump(mode="json")
                result["persisted"] = True
        typer.echo(json.dumps(result, indent=2))
    except (ValueError, KeyError, IndexError, TypeError, OSError, FirmsError, ConfigurationError):
        typer.echo(
            "Invalid source evidence, normalization policy or region configuration.", err=True
        )
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo("Normalization database transaction failed and was rolled back.", err=True)
        raise typer.Exit(1) from None
    finally:
        if database is not None:
            database.close()
        close()


@app.command("import-firms")
def import_firms_command(
    path: Annotated[Path, typer.Option("--path")],
    manifest: Annotated[Path, typer.Option("--manifest")],
    output: Annotated[Path, typer.Option("--output")] = Path("data/archive-imports"),
    chunk_rows: Annotated[int, typer.Option("--chunk-rows", min=1, max=5000)] = 1000,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    invalid_frp: Annotated[str, typer.Option("--invalid-frp")] = "reject",
    missing_frp: Annotated[str, typer.Option("--missing-frp")] = "allow",
) -> None:
    """Import a trusted local FIRMS CSV with explicit provenance and bounded commits."""
    from firewatch.logging import install_redaction
    from firewatch.repositories.archive import import_prepared
    from firewatch.sources.archive_manifest import ArchiveError, load_archive_manifest
    from firewatch.sources.archive_stage import prepare_archive
    from firewatch.sources.firms_normalize import NormalizationPolicy

    def close() -> None:
        pass

    database = None
    try:
        prepared = prepare_archive(
            path,
            load_archive_manifest(manifest),
            output,
            policy=NormalizationPolicy.model_validate(
                {"invalid_frp": invalid_frp, "missing_frp": missing_frp}
            ),
            chunk_rows=chunk_rows,
        )
        if dry_run:
            result = {
                "status": "validated",
                "import_id": str(prepared.import_id),
                "quality": prepared.quality,
                "evidence_directory": str(prepared.folder),
                "database_modified": False,
            }
        else:
            settings = get_settings()
            close = install_redaction(settings)
            database = Database(settings)
            report = import_prepared(prepared, database.engine, chunk_rows=chunk_rows)
            result = {
                "status": report["status"],
                "import_id": report["import_id"],
                "database": report["database"],
                "handoff_path": str(prepared.folder / "handoff.json"),
                "observation_history_complete": report["inventory"]["observation_history_complete"],
                "ready_for_supervised_training": False,
            }
        typer.echo(json.dumps(result, indent=2))
    except ArchiveError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    except (ValueError, TypeError, KeyError, OSError, OverflowError, ConfigurationError):
        typer.echo(
            "Invalid local archive, provenance manifest or normalization configuration.", err=True
        )
        raise typer.Exit(1) from None
    except sqlite3.Error:
        typer.echo("Archive staging index is unavailable. Check local storage and retry.", err=True)
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo(
            "Archive database write failed. Completed chunks are checkpointed; "
            "rerun the same input to resume.",
            err=True,
        )
        raise typer.Exit(1) from None
    finally:
        if database is not None:
            database.close()
        close()


@app.command("refresh-firms")
def refresh_firms_command(
    region: Annotated[str | None, typer.Option("--region")] = None,
    as_of: Annotated[str | None, typer.Option("--as-of")] = None,
    products: Annotated[list[str] | None, typer.Option("--product")] = None,
    chunk_days: Annotated[int, typer.Option("--chunk-days", min=1, max=5)] = 5,
    request_key: Annotated[str | None, typer.Option("--request-key")] = None,
    resume: Annotated[str | None, typer.Option("--resume")] = None,
    allow_disabled: Annotated[bool, typer.Option("--allow-disabled")] = False,
) -> None:
    """Run/resume the 48-hour FIRMS source stage; no pipeline or scoring run is completed."""
    from datetime import UTC, datetime
    from uuid import UUID

    from firewatch.logging import install_redaction
    from firewatch.pipeline.firms_refresh import refresh_firms
    from firewatch.schemas.firms_refresh import RefreshSpec
    from firewatch.sources.firms import FirmsClient

    def close() -> None:
        pass

    database = None
    client = None
    try:
        if resume and (
            region is not None or as_of is not None or products or request_key or chunk_days != 5
        ):
            raise ValueError("Resume uses its saved immutable specification")
        settings = get_settings()
        close = install_redaction(settings)
        database = Database(settings)
        if settings.firms_map_key is not None:
            client = FirmsClient(settings.firms_map_key, output_dir=settings.raw_data_dir / "firms")
        spec = (
            None
            if resume
            else RefreshSpec.model_validate(
                dict(
                    region=region or "northern-california",
                    as_of=as_of or datetime.now(UTC),
                    products=products or ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"],
                    chunk_days=chunk_days,
                    request_key=request_key,
                )
            )
        )
        result = refresh_firms(
            database.engine,
            client,
            spec,
            resume=UUID(resume) if resume else None,
            output=settings.raw_data_dir.parent / "firms-refresh",
            allow_disabled=allow_disabled,
            max_age_hours=settings.max_refresh_age_hours,
        )
        typer.echo(result.model_dump_json(indent=2))
        if result.coverage != "complete":
            raise typer.Exit(2)
    except (ValueError, OSError, KeyError, TypeError, OverflowError, ConfigurationError):
        typer.echo(
            "Invalid refresh configuration, disabled/missing region, unavailable source key, "
            "or conflicting saved request.",
            err=True,
        )
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo(
            "Refresh database unavailable. Committed chunks remain saved; "
            "resume using the refresh ID.",
            err=True,
        )
        raise typer.Exit(1) from None
    finally:
        if client is not None:
            client.close()
        if database is not None:
            database.close()
        close()


@app.command("import-weather")
def import_weather_command(
    latitude: Annotated[list[float], typer.Option("--latitude")],
    longitude: Annotated[list[float], typer.Option("--longitude")],
    start: Annotated[str, typer.Option("--start", help="Inclusive aware UTC hourly timestamp.")],
    end: Annotated[str, typer.Option("--end", help="Inclusive aware UTC hourly timestamp.")],
    product: Annotated[str, typer.Option("--product")] = "historical_forecast",
    source_version: Annotated[str, typer.Option("--source-version")] = "weather-v1",
    offline: Annotated[bool, typer.Option("--offline")] = False,
) -> None:
    """Cache up to eight cells/seven days; default historical mode never uses live weather."""
    from firewatch.repositories.weather import get_weather
    from firewatch.sources.open_meteo import OpenMeteoClient, WeatherRequest, weather_cell
    from firewatch.sources.weather_budget import WeatherBudget, WeatherError

    database = None
    client = None
    try:
        if len(latitude) != len(longitude) or not 1 <= len(latitude) <= 8:
            raise ValueError("Pair up to eight coordinates")
        cells = {
            cell.key: cell
            for cell in (
                weather_cell(lat, lon) for lat, lon in zip(latitude, longitude, strict=True)
            )
        }
        request = WeatherRequest.model_validate(
            dict(
                cells=list(cells.values()),
                start=start,
                end=end,
                product=product,
                model="era5" if product == "reanalysis" else "gfs_seamless",
                source_version=source_version,
                mode="live" if product == "live" else "replay",
            )
        )
        settings = get_settings()
        if settings.weather_cache_grid_degrees != 0.1:
            raise ValueError("This weather grid version requires 0.1 degrees")
        database = Database(settings)
        if not offline:
            client = OpenMeteoClient(
                settings.raw_data_dir / "weather",
                WeatherBudget(settings.raw_data_dir.parent / "weather-budget.sqlite"),
            )
        series = get_weather(database.engine, request, client=client, offline=offline)
        typer.echo(
            json.dumps(
                {
                    "status": "cached",
                    "product": product,
                    "cells": len(cells),
                    "records": len(series.records),
                    "missing_hours": sum(
                        "weather_hour_missing" in r.quality_flags for r in series.records
                    ),
                    "source_assets": sorted({str(r.asset_id) for r in series.records}),
                }
            )
        )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        sqlite3.Error,
        WeatherError,
        ConfigurationError,
    ):
        typer.echo(
            "Weather import failed: check interval, product, cache, source or budget.", err=True
        )
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo("Weather database unavailable; run migrations and retry.", err=True)
        raise typer.Exit(1) from None
    finally:
        if client is not None:
            client.close()
        if database is not None:
            database.close()


@app.command("build-baselines")
def build_baselines_command(
    latitude: Annotated[list[float] | None, typer.Option("--latitude")] = None,
    longitude: Annotated[list[float] | None, typer.Option("--longitude")] = None,
    region: Annotated[str | None, typer.Option("--region")] = None,
    config: Annotated[Path, typer.Option("--config")] = Path("configs/features-v1.yaml"),
    offline: Annotated[bool, typer.Option("--offline")] = False,
) -> None:
    """Precompute fixed-reference monthly POWER context; annual cache checkpoints allow resume."""
    from firewatch.processing.baselines import load_baseline_spec
    from firewatch.repositories.baselines import build_baselines
    from firewatch.repositories.regions import load_regions
    from firewatch.sources.power import PowerClient, climate_cell
    from firewatch.sources.weather_budget import WeatherBudget, WeatherError

    database = None
    client = None
    try:
        spec = load_baseline_spec(config)
        settings = get_settings()
        if region is not None:
            if latitude is not None or longitude is not None:
                raise ValueError("Use a region or explicit coordinates")
            regions = load_regions(settings.regions_config)
            area = next((r for r in regions.regions if r.name == region), None)
            if area is None:
                raise ValueError("Unknown configured region")
            box = area.bbox
            first = climate_cell(box.south, box.west)
            last = climate_cell(box.north - 1e-9, box.east - 1e-9)
            ny = round((last.latitude - first.latitude) / 0.5) + 1
            nx = round((last.longitude - first.longitude) / 0.625) + 1
            if nx * ny > 64:
                raise ValueError("Region exceeds 64 climate cells; use explicit cell chunks")
            cells = [
                climate_cell(first.latitude + y * 0.5, first.longitude + x * 0.625)
                for y in range(ny)
                for x in range(nx)
            ]
        else:
            if (
                not latitude
                or not longitude
                or len(latitude) != len(longitude)
                or len(latitude) > 64
            ):
                raise ValueError("Pair one to sixty-four coordinates")
            cells = [climate_cell(lat, lon) for lat, lon in zip(latitude, longitude, strict=True)]
        database = Database(settings)
        if not offline:
            client = PowerClient(
                settings.raw_data_dir / "power",
                WeatherBudget(settings.raw_data_dir.parent / "power-budget.sqlite"),
            )
        rows = build_baselines(
            database.engine,
            cells,
            spec,
            settings.raw_data_dir.parent / "climate-baselines",
            client=client,
            offline=offline,
        )
        typer.echo(
            json.dumps(
                {
                    "status": "cached",
                    "climate_cells": len({r.cell_key for r in rows}),
                    "monthly_baselines": len(rows),
                    "version": spec.version,
                    "reference_years": [spec.reference_start_year, spec.reference_end_year],
                    "missing_temperature_months": sum(r.temperature_mean_c is None for r in rows),
                    "missing_precipitation_months": sum(
                        r.precipitation_mean_mm is None for r in rows
                    ),
                }
            )
        )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        sqlite3.Error,
        WeatherError,
        ConfigurationError,
        yaml.YAMLError,
    ):
        typer.echo(
            "Climate baseline build failed: check config, cache, source or budget. "
            "Committed annual source chunks are retained; rerun to resume.",
            err=True,
        )
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo("Climate database unavailable; committed annual chunks are retained.", err=True)
        raise typer.Exit(1) from None
    finally:
        if client is not None:
            client.close()
        if database is not None:
            database.close()


@app.command("build-snapshots")
def build_snapshots_command(
    region: Annotated[str, typer.Option("--region")],
    as_of: Annotated[str, typer.Option("--as-of")],
    products: Annotated[list[str] | None, typer.Option("--product")] = None,
    retrieval_cutoff: Annotated[str | None, typer.Option("--retrieval-cutoff")] = None,
    config: Annotated[Path, typer.Option("--config")] = Path("configs/features-v1.yaml"),
    allow_disabled: Annotated[bool, typer.Option("--allow-disabled")] = False,
) -> None:
    """Build an idempotent replay snapshot stage from cached FIRMS data; no provider calls."""
    from datetime import UTC, datetime
    from uuid import NAMESPACE_URL, uuid5

    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert

    from firewatch.db.models import Region, Run
    from firewatch.processing.clusters import load_clustering_spec
    from firewatch.repositories.snapshots import build_run_snapshots, policy_for_snapshots
    from firewatch.schemas.common import Completeness, Product, RunMode, RunStatus, Sensor
    from firewatch.schemas.contracts import RunContext
    from firewatch.sources.firms_normalize import digest
    from firewatch.sources.open_meteo import utc

    database = None
    try:
        settings = get_settings()
        specification = load_clustering_spec(config)
        cutoff = RunContext.model_validate(
            dict(
                run_id="00000000-0000-4000-8000-000000000001",
                mode="replay",
                as_of=as_of,
                feature_version=settings.feature_schema_version,
            )
        ).as_of
        now = datetime.now(UTC)
        if cutoff > now:
            raise ValueError("Snapshot cutoff cannot be in the future")
        available = utc(datetime.fromisoformat(retrieval_cutoff)) if retrieval_cutoff else now
        if available > now:
            raise ValueError("Source availability cutoff cannot be in the future")
        choices = {}
        for value in products or ["VIIRS_NOAA20_SP"]:
            product = Product(value)
            sensor = Sensor.NOAA20 if "NOAA20" in product else Sensor.NOAA21
            if sensor in choices:
                raise ValueError("Select one unique product per sensor")
            choices[sensor] = product
        database = Database(settings)
        with database.transaction() as session:
            area = session.scalar(select(Region).where(Region.name == region))
            if area is None or (not area.enabled and not allow_disabled):
                raise ValueError("Unknown or disabled study region")
            policy = policy_for_snapshots(session, area.id, cutoff, choices, available)
            # Equal selected batches share logical identity across availability cutoffs.
            key = digest(
                dict(
                    region=str(area.id),
                    as_of=cutoff.isoformat(),
                    products={s.value: p.value for s, p in choices.items()},
                    batches=sorted(str(i) for i in policy.batch_ids),
                    config=specification.model_dump(mode="json"),
                    feature_version=settings.feature_schema_version,
                )
            )
            identifier = uuid5(NAMESPACE_URL, "firewatch-snapshots-v1:" + key)
            session.execute(
                insert(Run)
                .values(
                    id=identifier,
                    region_id=area.id,
                    mode=RunMode.REPLAY,
                    as_of=cutoff,
                    status=RunStatus.RUNNING,
                    config_hash=digest(specification.model_dump(mode="json")),
                    run_key="snapshot-stage:" + key,
                    completeness=Completeness.PARTIAL,
                    stage_status={},
                    input_asset_manifest={},
                    scorer_version=settings.active_scorer,
                    feature_version=settings.feature_schema_version,
                    started_at=now,
                )
                .on_conflict_do_nothing(index_elements=[Run.id])
            )
            saved = session.get(Run, identifier)
            prior = saved.input_asset_manifest.get("firms") if saved else None
            if prior is not None:
                from firewatch.repositories.revisions import RevisionPolicy

                policy = RevisionPolicy.model_validate(prior["policy"])
            result = build_run_snapshots(session, identifier, policy, specification)
        typer.echo(
            json.dumps(
                {
                    "status": "snapshots_succeeded",
                    "run_id": str(result.run_id),
                    "mode": result.mode,
                    "as_of": result.as_of.isoformat(),
                    "snapshots": len(result.snapshots),
                    "candidate_detections": result.candidate_count,
                    "history_detections": result.history_count,
                    "wide_clusters": sum(
                        "members_outside_centroid_radius" in d.quality_flags
                        for d in result.snapshots
                    ),
                    "snapshot_manifest_sha256": digest(result.model_dump(mode="json")),
                    "pipeline_completed": False,
                }
            )
        )
    except (ValueError, OSError, KeyError, TypeError, ConfigurationError, yaml.YAMLError):
        typer.echo(
            "Snapshot stage failed: check config, source-day coverage, region and pinned inputs.",
            err=True,
        )
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo("Snapshot database stage failed; its transaction was rolled back.", err=True)
        raise typer.Exit(1) from None
    finally:
        if database is not None:
            database.close()


@app.command("build-features")
def build_features_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    config: Annotated[Path, typer.Option("--config")] = Path("configs/features-v1.yaml"),
    retrieval_cutoff: Annotated[str | None, typer.Option("--retrieval-cutoff")] = None,
    fork_run: Annotated[bool, typer.Option("--fork")] = False,
) -> None:
    """Build immutable features from a saved snapshot Run and cached enrichment; no HTTP."""
    from datetime import UTC, datetime
    from uuid import UUID

    from firewatch.processing.features import load_feature_spec
    from firewatch.repositories.features import build_run_features, fork_feature_run
    from firewatch.sources.open_meteo import utc
    from firewatch.sources.weather_budget import WeatherError

    database = None
    try:
        identifier = UUID(run_id)
        specification = load_feature_spec(config)
        available = utc(datetime.fromisoformat(retrieval_cutoff)) if retrieval_cutoff else None
        if available is not None and available > datetime.now(UTC):
            raise ValueError("Feature availability cannot be in the future")
        settings = get_settings()
        database = Database(settings)
        with database.transaction() as session:
            if fork_run:
                identifier = fork_feature_run(session, identifier, specification)
            result = build_run_features(
                session, identifier, specification, retrieval_cutoff=available
            )
        typer.echo(
            json.dumps(
                {
                    "status": "features_succeeded",
                    "run_id": str(identifier),
                    "feature_sets": len(result),
                    "feature_version": specification.feature_version,
                    "missing_weather_sets": sum(f.values["temperature_c"] is None for f in result),
                    "missing_climate_sets": sum(
                        f.values["temperature_departure_c"] is None for f in result
                    ),
                    "feature_hashes": {str(f.snapshot_id): f.feature_hash for f in result},
                    "pipeline_completed": False,
                }
            )
        )
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        ConfigurationError,
        WeatherError,
        yaml.YAMLError,
    ):
        typer.echo(
            "Feature build failed: check run, frozen sources, config and cached enrichment.",
            err=True,
        )
        raise typer.Exit(1) from None
    except SQLAlchemyError:
        typer.echo("Feature database stage failed; its transaction was rolled back.", err=True)
        raise typer.Exit(1) from None
    finally:
        if database is not None:
            database.close()


@app.command("refresh")
def refresh_command(
    region: Annotated[str, typer.Option(help="Enabled configured region name")],
    idempotency_key: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Queue one durable live refresh; run python -m firewatch.worker separately."""
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from sqlalchemy import select

    from firewatch.api.errors import APIError
    from firewatch.db.models import Region
    from firewatch.repositories.jobs import enqueue, refresh_payload
    from firewatch.schemas.common import JobType
    from firewatch.schemas.operator import RefreshRequest

    database = None
    try:
        settings = get_settings()
        database = Database(settings)
        now = datetime.now(UTC)
        key = idempotency_key or str(uuid4())
        if not 8 <= len(key) <= 100 or not all(c.isalnum() or c in "_-" for c in key):
            raise ValueError("Use an 8-100 character alphanumeric idempotency key")
        with database.session() as session:
            area = session.scalar(select(Region).where(Region.name == region))
            if area is None:
                raise ValueError("Unknown configured region")
            area_id = area.id
        with database.session() as session:
            # A stable key resolves to its original request/cutoff on CLI retry.
            from firewatch.db.models import Job

            existing = session.scalar(select(Job).where(Job.idempotency_key == "operator:" + key))
            if existing is not None:
                if existing.type != JobType.REFRESH or existing.payload.get("region_id") != str(
                    area_id
                ):
                    raise ValueError("Idempotency key belongs to another request")
                body = RefreshRequest.model_validate(
                    {k: existing.payload[k] for k in ("region_id", "window_start", "window_end")}
                )
            else:
                body = RefreshRequest(
                    region_id=area_id, window_start=now - timedelta(hours=48), window_end=now
                )
        with database.session() as session:
            job, reused = enqueue(
                session,
                settings,
                JobType.REFRESH,
                key,
                body.model_dump(mode="json"),
                now,
                lambda: refresh_payload(session, settings, body, now),
            )
        typer.echo(
            json.dumps(
                {
                    "job_id": str(job.id),
                    "status": job.status.value,
                    "idempotency_key": key,
                    "reused": reused,
                }
            )
        )
    except (ConfigurationError, ValueError, OSError, SQLAlchemyError, APIError):
        typer.echo(
            "Refresh enqueue failed; verify configuration, region and idempotency key.", err=True
        )
        raise typer.Exit(1) from None
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    app()
