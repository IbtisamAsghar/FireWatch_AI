"""Operator entrypoint; --help/--version work before credentials are configured."""

import json
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
