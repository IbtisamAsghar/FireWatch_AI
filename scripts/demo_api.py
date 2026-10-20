"""Serve isolated synthetic Swagger fixtures: python -m scripts.demo_api --port 8001."""

import argparse
import os
import re
import secrets
from collections.abc import AsyncIterator, Generator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from uuid import uuid4

import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from firewatch.api.errors import error_response
from firewatch.config import ConfigurationError, Settings, get_settings
from firewatch.db.session import get_session
from firewatch.main import create_app
from firewatch.repositories.regions import load_regions, seed_regions
from scripts.demo_data import IDS, ROOT, seed_demo


def configured_demo() -> Settings:
    test_url = os.getenv("FIREWATCH_TEST_DATABASE_URL")
    if os.getenv("APP_ENV", "").lower() == "production":
        raise ValueError("Synthetic demo is disabled in production")
    if test_url:
        try:
            existing = get_settings()
        except ConfigurationError:
            existing = None
        if existing is not None and existing.app_env == "production":
            raise ValueError("Synthetic demo is disabled in production")
        token = (
            existing.admin_api_token.get_secret_value() if existing else secrets.token_urlsafe(48)
        )
        return Settings(
            database_url=test_url,
            admin_api_token=os.getenv("ADMIN_API_TOKEN") or token,
            app_env="test",
            public_reads_enabled=True,
            _env_file=None,
        )
    configured = get_settings()
    if configured.app_env == "production":
        raise ValueError("Synthetic demo is disabled in production")
    url = make_url(configured.database_url.get_secret_value()).set(database="firewatch_test")
    return configured.model_copy(
        update={
            "database_url": type(configured.database_url)(
                url.render_as_string(hide_password=False)
            ),
            "chain_enabled": False,
            "public_reads_enabled": True,
            "active_scorer": "baseline-v1",
            "firms_map_key": None,
            "chain_private_key": None,
            "cors_origins": [],
        }
    )


@contextmanager
def demo_schema(settings: Settings) -> Iterator[Engine]:
    url = make_url(settings.database_url.get_secret_value())
    if settings.app_env == "production" or not (url.database or "").endswith("_test"):
        raise ValueError("Demo requires a non-production disposable database ending in _test")
    schema = "fw_demo_" + uuid4().hex
    options = {"connect_timeout": 5, "options": "-c timezone=UTC -c statement_timeout=30000"}
    control = create_engine(url, hide_parameters=True, connect_args=options)
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={**options, "options": options["options"] + f" -csearch_path={schema},public"},
    )
    created = False
    try:
        with control.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            created = True
        migration = Config(str(ROOT / "alembic.ini"))
        with engine.begin() as connection:
            migration.attributes["connection"] = connection
            command.upgrade(migration, "head")
            command.check(migration)
        with Session(engine) as session, session.begin():
            config = load_regions(ROOT / "configs/regions.yaml")
            first = seed_regions(session, config)
            assert seed_regions(session, config) == first
            seed_demo(session)
            seed_demo(session)
        yield engine
    finally:
        engine.dispose()
        if created:
            assert re.fullmatch(r"fw_demo_[0-9a-f]{32}", schema)
            with control.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


class DemoReadOnly:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            await error_response(
                Request(scope),
                403,
                "SYNTHETIC_DEMO_READ_ONLY",
                "This synthetic demo permits read operations only.",
            )(scope, receive, send)
            return
        await self.app(scope, receive, send)


def create_demo_app(settings: Settings | None = None) -> FastAPI:
    configured = settings if settings is not None else configured_demo()
    if configured.app_env == "production":
        raise ValueError("Synthetic demo is disabled in production")
    configured = configured.model_copy(
        update={
            "public_reads_enabled": True,
            "active_scorer": "baseline-v1",
            "chain_enabled": False,
        }
    )
    application = create_app(configured)
    application.title = "FireWatch AI — SYNTHETIC acceptance demo"
    application.description = (
        "Invented software acceptance data only; no real fire observations, "
        "trained predictions or provider/chain calls. All mutations disabled. "
        "The clock is fixed at 2025-06-11T12:03:00Z. " + application.description
    )
    application.add_middleware(DemoReadOnly)
    original_lifespan = application.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with original_lifespan(app):
            with demo_schema(configured) as engine:

                def sessions() -> Generator[Session, None, None]:
                    with Session(engine) as session:
                        yield session

                app.dependency_overrides[get_session] = sessions
                app.state.clock = lambda: datetime(2025, 6, 11, 12, 3, tzinfo=UTC)
                app.state.demo_engine = engine
                try:
                    yield
                finally:
                    app.dependency_overrides.pop(get_session, None)

    application.router.lifespan_context = lifespan
    original_openapi = application.openapi

    def openapi():
        document = original_openapi()
        examples = {
            "run_id": IDS["live"],
            "region_id": IDS["region"],
            "snapshot_id": IDS["live-critical"],
            "alert_id": IDS["alert"],
            "job_id": IDS["job"],
        }
        for operations in document["paths"].values():
            for operation in operations.values():
                if not isinstance(operation, dict):
                    continue
                if operation.get("operationId", "").startswith("queue_"):
                    operation.setdefault("responses", {})["403"] = {
                        "description": "All mutations are disabled in this isolated synthetic demo",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ErrorEnvelope"}
                            }
                        },
                    }
                for parameter in operation.get("parameters", []):
                    if parameter["name"] in examples:
                        parameter["example"] = str(examples[parameter["name"]])
        return document

    application.openapi = openapi
    return application


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    uvicorn.run(create_demo_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
