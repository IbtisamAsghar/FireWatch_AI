"""ASGI body bounds and authentication before JSON parsing; no payload logging."""

import asyncio
import json
import secrets

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from firewatch.api.errors import error_response
from firewatch.config import Settings


def authenticated(values: list[str], expected: str) -> bool:
    if len(values) != 1:
        return False
    scheme, separator, token = values[0].partition(" ")
    supplied = token.encode() if separator and scheme.lower() == "bearer" else b""
    return secrets.compare_digest(supplied, expected.encode())


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def invalid_constant(value: str) -> object:
    raise ValueError("Non-finite JSON constant")


class OperatorBoundary:
    """Bound actual streamed bytes, even without or despite Content-Length."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}:
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        settings: Settings = scope["app"].state.settings

        async def reject(status: int, code: str, message: str) -> None:
            headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
            await error_response(request, status, code, message, headers=headers)(
                scope, receive, send
            )

        if scope["path"].startswith("/api/v1/") and not authenticated(
            request.headers.getlist("authorization"), settings.admin_api_token.get_secret_value()
        ):
            await reject(401, "UNAUTHORIZED", "A valid operator bearer token is required.")
            return
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or (
            lengths
            and (not lengths[0].isascii() or not lengths[0].isdigit() or len(lengths[0]) > 10)
        ):
            await reject(400, "INVALID_CONTENT_LENGTH", "Invalid Content-Length header.")
            return
        if lengths and int(lengths[0]) > settings.max_request_body_bytes:
            await reject(413, "REQUEST_TOO_LARGE", "The request body exceeds the configured limit.")
            return
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            await reject(
                415, "UNSUPPORTED_ENCODING", "Compressed request bodies are not supported."
            )
            return
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/json"
        ):
            await reject(415, "JSON_REQUIRED", "Operator requests require application/json.")
            return
        body = bytearray()
        try:
            async with asyncio.timeout(settings.request_body_timeout_seconds):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > settings.max_request_body_bytes:
                        await reject(
                            413,
                            "REQUEST_TOO_LARGE",
                            "The request body exceeds the configured limit.",
                        )
                        return
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await reject(408, "REQUEST_TIMEOUT", "The request body did not arrive in time.")
            return
        if lengths and int(lengths[0]) != len(body):
            await reject(
                400, "CONTENT_LENGTH_MISMATCH", "The request body length does not match its header."
            )
            return
        try:
            parsed = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=invalid_constant,
            )
            if not isinstance(parsed, dict):
                raise ValueError("JSON object required")
        except (ValueError, UnicodeError, RecursionError):
            await reject(
                422, "VALIDATION_ERROR", "The request must contain an unambiguous JSON object."
            )
            return
        delivered = False

        async def bounded_receive() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, bounded_receive, send)
