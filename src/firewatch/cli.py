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
