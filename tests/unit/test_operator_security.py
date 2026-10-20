"""Security boundary contracts, actual streamed limits and process log redaction."""

import asyncio
import logging
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from firewatch.api.security import OperatorBoundary
from firewatch.config import Settings
from firewatch.logging import install_redaction, redact
from firewatch.main import create_app

TOKEN = "unit-operator-" + "x" * 40


def settings(**kwargs):
    return Settings(
        database_url="postgresql+psycopg://test:private-password@localhost:1/fw",
        admin_api_token=TOKEN,
        _env_file=None,
        **kwargs,
    )


def boundary(messages, headers=None, slow=False):
    async def exercise():
        configured = settings(max_request_body_bytes=1024, request_body_timeout_seconds=1)
        response, downstream = [], []
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/jobs/refresh",
            "query_string": b"",
            "headers": headers
            or [
                (b"authorization", ("Bearer " + TOKEN).encode()),
                (b"content-type", b"application/json"),
            ],
            "app": SimpleNamespace(state=SimpleNamespace(settings=configured)),
            "state": {},
        }
        iterator = iter(messages)

        async def receive():
            if slow:
                await asyncio.sleep(2)
            return next(iterator)

        async def send(message):
            response.append(message)

        async def app(scope, receive, send):
            downstream.append((await receive())["body"])
            await send({"type": "http.response.start", "status": 200, "headers": []})

        await OperatorBoundary(app)(scope, receive, send)
        return response, downstream

    return asyncio.run(exercise())


def test_actual_streamed_size_limits_and_bounded_body_forwarding():
    response, downstream = boundary(
        [
            {"type": "http.request", "body": b"x" * 600, "more_body": True},
            {"type": "http.request", "body": b"x" * 600, "more_body": False},
        ]
    )
    assert response[0]["status"] == 413 and not downstream
    response, downstream = boundary(
        [
            {"type": "http.request", "body": b"{", "more_body": True},
            {"type": "http.request", "body": b"}", "more_body": False},
        ]
    )
    assert response[0]["status"] == 200 and downstream == [b"{}"]


@pytest.mark.parametrize(
    "lengths,expected",
    [
        ([b"1025"], 413),
        ([b"invalid"], 400),
        ([b"2", b"2"], 400),
        ([b"1"], 400),
    ],
)
def test_invalid_declared_lengths_never_reach_router(lengths, expected):
    headers = [
        (b"authorization", ("Bearer " + TOKEN).encode()),
        (b"content-type", b"application/json"),
    ]
    headers.extend((b"content-length", value) for value in lengths)
    response, downstream = boundary([{"type": "http.request", "body": b"{}"}], headers=headers)
    assert response[0]["status"] == expected and not downstream


def test_slow_or_disconnected_body_is_bounded():
    response, downstream = boundary([{"type": "http.request", "body": b"{}"}], slow=True)
    assert response[0]["status"] == 408 and not downstream
    response, downstream = boundary([{"type": "http.disconnect"}])
    assert not response and not downstream


@pytest.mark.parametrize(
    "body", [b"{bad-json", b'{"a":1,"a":2}', b"[]", b'{"a":NaN}', b'{"a":Infinity}']
)
def test_ambiguous_or_non_json_operator_bodies_are_rejected(body):
    response, downstream = boundary([{"type": "http.request", "body": body}])
    assert response[0]["status"] == 422 and not downstream


def test_cors_operator_preflight_is_explicit_and_exposes_job_location():
    app = create_app(settings(cors_origins=["https://frontend.example"]))
    with TestClient(app) as client:
        headers = {
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization,Content-Type,Idempotency-Key",
        }
        response = client.options("/api/v1/jobs/refresh", headers=headers)
        assert response.status_code == 200
        assert response.headers["Access-Control-Allow-Origin"] == "https://frontend.example"
        assert "Access-Control-Allow-Credentials" not in response.headers
        headers["Origin"] = "https://untrusted.example"
        assert client.options("/api/v1/jobs/refresh", headers=headers).status_code == 400
        response = client.get("/health/live", headers={"Origin": "https://frontend.example"})
        assert "Location" in response.headers["Access-Control-Expose-Headers"]


def test_all_mutations_have_required_auth_and_idempotency_in_openapi():
    document = create_app().openapi()
    posts = [
        operations["post"] for operations in document["paths"].values() if "post" in operations
    ]
    assert {operation["operationId"] for operation in posts} == {
        "queue_refresh",
        "queue_replay",
        "queue_anchor",
    }
    for operation in posts:
        assert operation["security"] == [{"OperatorBearer": []}]
        assert operation["responses"]["202"]["content"]["application/json"]["schema"]
        key = next(item for item in operation["parameters"] if item["name"] == "Idempotency-Key")
        assert key["required"] and key["in"] == "header"
    assert len(document["paths"]) == 18
    assert not any(
        any(term in path for term in ("train", "promote", "import")) for path in document["paths"]
    )


@pytest.mark.parametrize("token", ["x" * 257, "private secret " * 4, "é" * 32])
def test_operator_token_has_bounded_header_safe_format(token):
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql+psycopg://test:pass@localhost/fw",
            admin_api_token=token,
            _env_file=None,
        )


def test_logging_redacts_values_paths_exceptions_and_late_handlers(caplog):
    configured = settings(firms_map_key="FIRMS-PRIVATE-KEY", chain_private_key="c" * 64)
    before = logging.getLogRecordFactory()
    close = install_redaction(configured)
    try:
        logger = logging.getLogger("new.provider.logger")
        with caplog.at_level(logging.INFO):
            logger.info("Authorization: Bearer %s", TOKEN)
            logger.info(
                "FIRMS https://firms.modaps.eosdis.nasa.gov/api/area/csv/UNKNOWN-KEY/NOAA20/1"
            )
            logger.info("/api/area/csv/UNKNOWN-PATH-KEY/NOAA20/1")
            logger.info("database=%s key=%s", configured.database_url.get_secret_value(), "c" * 64)
            logger.info("encoded=%s", quote(configured.database_url.get_secret_value(), safe=""))
            try:
                raise RuntimeError("FIRMS-PRIVATE-KEY private-password")
            except RuntimeError:
                logger.exception("request failed")
        for secret in (
            TOKEN,
            "UNKNOWN-KEY",
            "UNKNOWN-PATH-KEY",
            "FIRMS-PRIVATE-KEY",
            "private-password",
            "c" * 64,
        ):
            assert secret not in caplog.text
        record = logging.getLogRecordFactory()(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1", "GET", "/?token=unknown-credential", "1.1", 200),
            None,
        )
        assert "unknown-credential" not in record.getMessage()
        assert isinstance(record.args[-1], int)
    finally:
        close()
    assert logging.getLogRecordFactory() is before
    assert "PRIVATE" not in redact("Authorization=PRIVATE password=PRIVATE")


def test_multiple_app_logging_registration_keeps_remaining_secrets():
    before = logging.getLogRecordFactory()
    close_first = install_redaction(settings())
    other = settings().model_copy(update={"admin_api_token": settings().admin_api_token})
    close_other = install_redaction(other)
    close_first()
    assert logging.getLogRecordFactory() is not before
    close_other()
    assert logging.getLogRecordFactory() is before


def test_production_requires_explicit_public_read_opt_in():
    assert settings().public_reads_enabled is True
    assert settings(app_env="production").public_reads_enabled is False
    assert settings(app_env="production", public_reads_enabled=True).public_reads_enabled is True


def test_unknown_basic_authorization_is_also_redacted():
    assert "unknown-base64-credential" not in redact(
        "Authorization: Basic unknown-base64-credential"
    )


def test_logging_registration_is_released_if_database_initialization_fails(monkeypatch):
    before = logging.getLogRecordFactory()

    def unavailable_database(*args, **kwargs):
        raise RuntimeError("synthetic startup failure")

    monkeypatch.setattr("firewatch.main.Database", unavailable_database)
    with pytest.raises(RuntimeError, match="synthetic startup failure"):
        with TestClient(create_app(settings())):
            pass
    assert logging.getLogRecordFactory() is before


def test_uvicorn_arguments_and_normalized_private_keys_are_redacted():
    close = install_redaction(settings(chain_private_key="0x" + "Ab" * 32))
    try:
        factory = logging.getLogRecordFactory()
        record = factory(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            ("127.0.0.1", TOKEN, "/", "1.1", 405),
            None,
        )
        assert TOKEN not in record.getMessage()
        record = factory("provider", logging.INFO, __file__, 1, "%s", ("ab" * 32,), None)
        assert "ab" * 32 not in record.getMessage()
    finally:
        close()
