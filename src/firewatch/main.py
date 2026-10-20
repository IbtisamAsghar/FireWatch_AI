"""Application bootstrap: configuration at startup, dependency-free liveness."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.types import ASGIApp, Receive, Scope, Send

from firewatch.config import Settings, get_settings
from firewatch.db.session import Database


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
            allow_methods=["GET"],
            allow_headers=["Content-Type"],
        )
        await middleware(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        configured = settings if settings is not None else get_settings()
        application.state.settings = configured
        database = Database(configured)
        application.state.database = database
        try:
            yield
        finally:
            database.close()

    application = FastAPI(
        title="FireWatch AI",
        version="0.1.0",
        description=(
            "Research prototype. Not for emergency response or protection of life/property."
        ),
        lifespan=lifespan,
    )
    application.add_middleware(ConfiguredCORS)

    @application.get("/health/live", response_model=LivenessResponse, operation_id="health_live")
    def live() -> LivenessResponse:
        return LivenessResponse()

    return application


def configured_app() -> FastAPI:
    """Uvicorn --factory entrypoint with configured middleware and safe validation."""
    return create_app(get_settings())


# Settings and the connection pool are initialized in lifespan, never on import.
app = create_app()
