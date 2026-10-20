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
