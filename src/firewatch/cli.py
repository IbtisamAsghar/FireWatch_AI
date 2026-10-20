"""Operator entrypoint; --help/--version work before credentials are configured."""

import json
from importlib.metadata import version

import typer
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
