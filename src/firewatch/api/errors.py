"""Uniform, credential-safe API errors with server-generated correlation IDs."""

import logging
import traceback
from collections.abc import Mapping
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("firewatch.api")


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False) -> None:
        self.status, self.code, self.message, self.retryable = status, code, message, retryable


def error_response(
    request: Request,
    status: int,
    code: str,
    message: str,
    retryable: bool = False,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=status,
        headers={"X-Request-ID": request_id, **(headers or {})},
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
                "retryable": retryable,
            }
        },
    )


def install_errors(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError) -> JSONResponse:
        return error_response(request, exc.status, exc.code, exc.message, exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, 422, "VALIDATION_ERROR", "Invalid path or query parameters.")

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.warning("Database unavailable request_id=%s", request.state.request_id)
        return error_response(
            request,
            503,
            "DATABASE_UNAVAILABLE",
            "The database is unavailable or not migrated.",
            True,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code = {
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            405: "METHOD_NOT_ALLOWED",
        }.get(exc.status_code, "HTTP_ERROR")
        message = {
            401: "A valid operator bearer token is required.",
            403: "This request is not permitted.",
            404: "The requested resource was not found.",
            405: "This HTTP method is not supported.",
        }.get(exc.status_code, "Request failed.")
        return error_response(request, exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # Traceback frames are useful diagnostics; exception values/SQL/inputs may contain secrets.
        frames = "\n".join(
            f"{frame.filename}:{frame.lineno} in {frame.name}"
            for frame in traceback.extract_tb(exc.__traceback__)
        )
        logger.error(
            "Unhandled %s request_id=%s frames=%s",
            type(exc).__name__,
            getattr(request.state, "request_id", "unknown"),
            frames,
        )
        return error_response(request, 500, "INTERNAL_ERROR", "An internal error occurred.")
