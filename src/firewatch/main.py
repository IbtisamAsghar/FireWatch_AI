"""Application bootstrap: configuration at startup, dependency-free liveness."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from firewatch.api.errors import install_errors
from firewatch.api.presentation import ArtifactInspector
from firewatch.api.routes.operator import router as mutations
from firewatch.api.routes.reads import health, operator, router
from firewatch.api.security import OperatorBoundary
from firewatch.config import Settings, get_settings
from firewatch.db.session import Database
from firewatch.evidence.chain_rpc import RpcAnchorReader
from firewatch.logging import install_redaction


class LivenessResponse(BaseModel):
    status: str = "ok"


class ConfiguredCORS:
    """Use startup-validated origins without requiring secrets at module import."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings: Settings = scope["app"].state.settings
        middleware = CORSMiddleware(
            self.app,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Authorization", "Idempotency-Key"],
            expose_headers=["X-Request-ID", "Location", "Idempotency-Replayed"],
        )
        await middleware(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configured = settings if settings is not None else get_settings()
        application.state.settings = configured
        close_redaction = install_redaction(configured)
        application.state.clock = lambda: datetime.now(UTC)
        application.state.artifacts = ArtifactInspector()
        reader = RpcAnchorReader(configured) if configured.chain_enabled else None
        application.state.anchor_reader = reader
        try:
            database = Database(configured)
            application.state.database = database
            try:
                yield
            finally:
                database.close()
        finally:
            if reader is not None:
                reader.close()
            close_redaction()

    application = FastAPI(
        title="FireWatch AI",
        version="0.1.0",
        description=(
            "Research prototype. Not for emergency response or protection of life/property."
        ),
        lifespan=lifespan,
    )
    application.add_middleware(OperatorBoundary)
    application.add_middleware(ConfiguredCORS)
    install_errors(application)
    application.include_router(health)
    application.include_router(router)
    application.include_router(operator)
    application.include_router(mutations)

    @application.get("/health/live", response_model=LivenessResponse, operation_id="health_live")
    def live() -> LivenessResponse:
        return LivenessResponse()

    def openapi() -> dict[str, Any]:
        if application.openapi_schema is None:
            document = get_openapi(
                title=application.title,
                version=application.version,
                description=application.description,
                routes=application.routes,
            )
            for path, operations in document["paths"].items():
                if path.startswith("/api/v1/") and not path.startswith("/api/v1/jobs/"):
                    for method, operation in operations.items():
                        if (
                            method == "get"
                            and isinstance(operation, dict)
                            and "operationId" in operation
                        ):
                            operation["security"] = [{}, {"OperatorBearer": []}]
                            operation["description"] = (
                                "Public when PUBLIC_READS_ENABLED=true (local prototype default); "
                                "otherwise requires the server operator bearer token."
                            )
            application.openapi_schema = document
        return application.openapi_schema

    application.openapi = openapi  # type: ignore[method-assign]
    return application


def configured_app() -> FastAPI:
    """Uvicorn --factory entrypoint with configured middleware and safe validation."""
    return create_app(get_settings())


# Settings and the connection pool are initialized in lifespan, never on import.
app = create_app()
