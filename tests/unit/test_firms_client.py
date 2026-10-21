"""FIRMS protocol tests use invented responses and never contact NASA."""

import hashlib
import json
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from firewatch.sources.firms import (
    FirmsAreaRequest,
    FirmsClient,
    FirmsError,
    FirmsPolicy,
    QuotaLedger,
)

KEY = "synthetic-firms-test-key-1234567890"
HEADER = (
    "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,"
    "instrument,confidence,version,bright_ti5,frp,daynight\n"
)
ROW = "39,-121,320.1,0.4,0.5,2022-09-07,0037,N20,VIIRS,n,2,280.1,10.2,N\n"
QUOTA = {"transaction_limit": 5000, "current_transactions": 0, "transaction_interval": "10 minutes"}


class Clock:
    def __init__(self):
        self.seconds = 0.0
        self.waits = []

    def now(self):
        return datetime(2026, 10, 21, 12, tzinfo=UTC) + timedelta(seconds=self.seconds)

    def mono(self):
        return self.seconds

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.seconds += seconds


def request(**changes):
    values = {
        "product": "VIIRS_NOAA20_SP",
        "start_date": "2022-09-07",
        "days": 1,
        "bbox": {"west": -122.5, "south": 38.5, "east": -120, "north": 40.5},
    }
    return FirmsAreaRequest.model_validate(values | changes)


def client(path, handler, clock=None, **policy):
    clock = clock or Clock()
    return FirmsClient(
        SecretStr(KEY),
        path,
        policy=FirmsPolicy(**policy),
        transport=httpx.MockTransport(handler),
        now=clock.now,
        monotonic=clock.mono,
        sleep=clock.sleep,
        jitter=lambda low, high: high / 4,
    )


def routing(area=None, quota=None):
    def handler(req):
        assert req.url.host == "firms.modaps.eosdis.nasa.gov" and req.url.scheme == "https"
        assert req.headers["accept-encoding"] == "identity"
        assert req.extensions["timeout"]["connect"] <= 5
        assert req.extensions["timeout"]["read"] <= 20
        if req.url.path.startswith("/mapserver/"):
            return httpx.Response(200, json=QUOTA if quota is None else quota)
        return httpx.Response(200, content=(HEADER + ROW).encode()) if area is None else area(req)

    return handler


@pytest.mark.parametrize(
    "changes",
    [
        {"days": 0},
        {"days": 6},
        {"days": True},
        {"days": "2"},
        {"product": "https://attacker.invalid/"},
        {"host": "http://attacker.invalid"},
        {"start_date": 1662508800},
        {"start_date": "2022-09-07T00:00:00Z"},
        {"start_date": "2022-09-31"},
        {"start_date": date(2017, 1, 1)},
        {"start_date": "9999-12-31", "days": 5},
        {"product": "VIIRS_NOAA21_SP", "start_date": "2022-09-07"},
        {"bbox": {"west": -180, "south": -90, "east": 180, "north": 90}},
        {"bbox": {"west": -120, "south": 38, "east": -122, "north": 40}},
    ],
)
def test_invalid_requests_never_reach_http(changes):
    with pytest.raises(ValidationError):
        request(**changes)


def test_future_and_mutated_requests_are_revalidated_before_io(tmp_path):
    def unexpected(req):
        pytest.fail("Invalid input reached HTTP")

    with client(tmp_path, unexpected) as api:
        with pytest.raises(ValueError):
            api.fetch(request(start_date="2026-10-22"))
        altered = request()
        altered.bbox.west = -180
        with pytest.raises(ValidationError):
            api.fetch(altered)
    assert not list(tmp_path.glob("*/manifest.json"))


@pytest.mark.parametrize(
    "content,empty,count",
    [
        (HEADER.encode(), True, 0),
        ((HEADER + ROW).encode(), False, 1),
        (b"\xef\xbb\xbf" + (HEADER + ROW).encode(), False, 1),
    ],
)
def test_typed_batch_keeps_raw_bytes_and_lexical_values(tmp_path, content, empty, count):
    with client(tmp_path, routing(lambda req: httpx.Response(200, content=content))) as api:
        batch = api.fetch(request())
    assert batch.empty is empty and batch.row_count == count
    raw = tmp_path / str(batch.batch_id) / "response-02.bin"
    assert raw.read_bytes() == content
    assert batch.asset.content_hash == hashlib.sha256(content).hexdigest()
    assert batch.asset.product == "VIIRS_NOAA20_SP"
    assert batch.retrieved_at.utcoffset().total_seconds() == 0
    if count:
        assert batch.rows[0]["acq_time"] == "0037" and batch.rows[0]["confidence"] == "n"
        assert batch.processing_versions == ["2"]
    manifest = json.loads(batch.manifest_path.read_text())
    assert manifest["status"] == ("empty" if empty else "succeeded")
    assert len(manifest["responses"]) == 2
    assert sum(x["reserved_transaction_units"] for x in manifest["responses"]) == 1100
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


@pytest.mark.parametrize(
    "body,code",
    [
        (b"", "MALFORMED_CSV"),
        (b"<html>service unavailable</html>", "PROVIDER_ERROR"),
        (b"Invalid MAP_KEY", "AUTHENTICATION"),
        (b"Transaction limit exceeded", "RATE_LIMITED"),
        (b"[]", "MALFORMED_CSV"),
        (b"\xff\xfe", "MALFORMED_CSV"),
        ((HEADER.replace("frp,", "latitude,") + ROW).encode(), "MALFORMED_CSV"),
        ((HEADER + ROW.rstrip() + ",extra\n").encode(), "MALFORMED_CSV"),
    ],
)
def test_provider_errors_and_malformed_data_cannot_be_empty_success(tmp_path, body, code):
    with client(
        tmp_path, routing(lambda req: httpx.Response(200, content=body)), max_attempts=1
    ) as api:
        with pytest.raises(FirmsError) as raised:
            api.fetch(request())
    assert raised.value.code == code
    manifest = json.loads(raised.value.manifest_path.read_text())
    assert manifest["status"] == "failed"
    assert (raised.value.manifest_path.parent / "response-02.bin").read_bytes() == body


@pytest.mark.parametrize("status", [401, 403, 404, 301, 302, 307])
def test_permanent_http_failures_and_redirects_are_not_retried(tmp_path, status):
    calls = []

    def area(req):
        calls.append(req)
        return httpx.Response(status, headers={"location": "https://attacker.invalid"})

    clock = Clock()
    with client(tmp_path, routing(area), clock) as api, pytest.raises(FirmsError):
        api.fetch(request())
    assert len(calls) == 1 and clock.waits == []


@pytest.mark.parametrize("status", [408, 425, 500, 502, 503, 504])
def test_transient_failures_use_bounded_exponential_backoff_and_preserve_attempts(tmp_path, status):
    attempts = []

    def area(req):
        attempts.append(req)
        return httpx.Response(status if len(attempts) < 3 else 200, content=(HEADER + ROW).encode())

    clock = Clock()
    with client(tmp_path, routing(area), clock) as api:
        batch = api.fetch(request())
    assert len(attempts) == 3 and clock.waits == [1.25, 2.25]
    manifest = json.loads(batch.manifest_path.read_text())
    assert len(manifest["responses"]) == 6
    assert manifest["attempts"] == 3


@pytest.mark.parametrize("http_date", [False, True])
def test_retry_after_seconds_and_http_dates_are_honored(tmp_path, http_date):
    clock = Clock()
    delay = format_datetime(clock.now() + timedelta(seconds=4)) if http_date else "4"
    calls = []

    def area(req):
        calls.append(req)
        return (
            httpx.Response(429, headers={"retry-after": delay})
            if len(calls) == 1
            else httpx.Response(200, content=HEADER.encode())
        )

    with client(tmp_path, routing(area), clock) as api:
        assert api.fetch(request()).empty
    assert clock.waits == [4]


def test_long_rate_limit_persists_across_client_restarts_without_sleeping(tmp_path):
    clock = Clock()
    with client(tmp_path, routing(lambda req: httpx.Response(429)), clock) as api:
        with pytest.raises(FirmsError) as raised:
            api.fetch(request())
    assert raised.value.retry_after_seconds == 600 and clock.waits == []

    def unexpected(req):
        pytest.fail("Persisted cooldown was bypassed")

    with client(tmp_path, unexpected, clock) as api, pytest.raises(FirmsError) as again:
        api.fetch(request())
    assert again.value.code == "RATE_LIMITED"


def test_exhausted_attempts_are_not_reported_as_empty(tmp_path):
    with client(tmp_path, routing(lambda req: httpx.Response(503))) as api:
        with pytest.raises(FirmsError) as raised:
            api.fetch(request())
    report = json.loads(raised.value.manifest_path.read_text())
    assert report["status"] == "failed" and report["attempts"] == 3
    assert len(report["responses"]) == 6


@pytest.mark.parametrize(
    "quota",
    [
        {**QUOTA, "current_transactions": 4500},
        {**QUOTA, "transaction_limit": 500},
    ],
)
def test_remote_quota_blocks_area_call_and_fails_closed(tmp_path, quota):
    def unexpected(req):
        pytest.fail("Exhausted remote budget reached the Area API")

    with client(tmp_path, routing(unexpected, quota)) as api, pytest.raises(FirmsError) as error:
        api.fetch(request())
    assert error.value.code == "RATE_LIMITED"


@pytest.mark.parametrize(
    "quota",
    [
        {},
        [],
        {**QUOTA, "current_transactions": "0"},
        {**QUOTA, "current_transactions": True},
        {**QUOTA, "transaction_interval": "1 minute"},
    ],
)
def test_malformed_quota_status_never_allows_area_request(tmp_path, quota):
    with client(tmp_path, routing(quota=quota)) as api, pytest.raises(FirmsError) as error:
        api.fetch(request())
    assert error.value.code == "STATUS_SCHEMA"


def test_quota_reservations_are_atomic_and_expire(tmp_path):
    path = tmp_path / "quota.sqlite3"

    def reserve(_):
        ledger = QuotaLedger(path, "key-fingerprint", 4000)
        try:
            ledger.reserve(1000, 1000)
            return True
        except FirmsError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(8))) == 4
    QuotaLedger(path, "different-key", 4000).reserve(4000, 1000)
    QuotaLedger(path, "key-fingerprint", 4000).reserve(4000, 1600)


def test_transport_errors_are_bounded_and_cannot_expose_urls(tmp_path, caplog):
    calls = []

    def failed(req):
        calls.append(req)
        logging.getLogger("httpcore").warning("request %s", req.url)
        raise httpx.ReadTimeout(f"Private URL: {req.url}")

    original = logging.getLogRecordFactory()
    with caplog.at_level(logging.DEBUG), client(tmp_path, failed) as api:
        with pytest.raises(FirmsError) as raised:
            api.fetch(request())
    assert len(calls) == 3
    assert KEY not in caplog.text
    assert KEY not in "".join(traceback.format_exception(raised.value))
    assert logging.getLogRecordFactory() is original


def test_credential_echo_is_discarded_not_saved_or_retried(tmp_path):
    with client(tmp_path, routing(lambda req: httpx.Response(200, text=KEY))) as api:
        with pytest.raises(FirmsError) as raised:
            api.fetch(request())
    report = json.loads(raised.value.manifest_path.read_text())
    assert raised.value.code == "CREDENTIAL_ECHO"
    assert report["responses"][-1]["body_discarded"]
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


def test_response_and_row_caps_retain_failed_evidence(tmp_path):
    with client(
        tmp_path, routing(lambda req: httpx.Response(200, content=b"x" * 2048)), max_bytes=1024
    ) as api:
        with pytest.raises(FirmsError) as error:
            api.fetch(request())
    assert error.value.code == "RESPONSE_LIMIT"
    assert json.loads(error.value.manifest_path.read_text())["responses"][-1]["complete"] is False
    with client(
        tmp_path, routing(lambda req: httpx.Response(200, text=HEADER + ROW * 2)), max_rows=1
    ) as api:
        with pytest.raises(FirmsError) as error:
            api.fetch(request())
    assert error.value.code == "RESPONSE_LIMIT"
    assert (error.value.manifest_path.parent / "response-02.bin").read_text() == HEADER + ROW * 2


def test_partial_transfer_evidence_is_not_success(tmp_path):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"latitude,longitude"
            raise httpx.ReadError("Disconnected")

    with client(
        tmp_path, routing(lambda req: httpx.Response(200, stream=BrokenStream())), max_attempts=1
    ) as api:
        with pytest.raises(FirmsError) as error:
            api.fetch(request())
    report = json.loads(error.value.manifest_path.read_text())
    assert error.value.code == "NETWORK" and not report["responses"][-1]["complete"]
    assert (
        error.value.manifest_path.parent / "response-02.bin"
    ).read_bytes() == b"latitude,longitude"


def test_operation_deadline_prevents_additional_attempts(tmp_path):
    clock = Clock()

    def slow(req):
        clock.seconds += 2
        return httpx.Response(503)

    with client(tmp_path, slow, clock, operation_seconds=1) as api:
        with pytest.raises(FirmsError) as error:
            api.fetch(request())
    assert error.value.code in {"HTTP_ERROR", "DEADLINE"}
    assert clock.waits == []


def test_closed_client_rejects_further_calls(tmp_path):
    with client(tmp_path, routing()) as api:
        pass
    with pytest.raises(FirmsError, match="closed"):
        api.fetch(request())


def test_source_values_are_not_normalized_in_this_phase(tmp_path):
    content = (HEADER + ROW.replace("39,-121", "invalid,unconverted")).encode()
    with client(tmp_path, routing(lambda req: httpx.Response(200, content=content))) as api:
        batch = api.fetch(request())
    assert batch.rows[0]["latitude"] == "invalid"
    assert batch.asset.format == "csv"


def test_cli_fetch_uses_configured_region_and_secret_without_database(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from firewatch.cli import app
    from firewatch.config import Settings

    settings = Settings(
        database_url="postgresql+psycopg://test:password@localhost:1/firewatch_test",
        admin_api_token="synthetic-test-operator-" + "x" * 40,
        firms_map_key=KEY,
        raw_data_dir=tmp_path,
        _env_file=None,
    )
    monkeypatch.setattr("firewatch.cli.get_settings", lambda: settings)
    monkeypatch.chdir(Path(__file__).resolve().parents[2])

    def provider(key, output_dir):
        assert key.get_secret_value() == KEY and output_dir == tmp_path / "firms"
        return client(output_dir, routing())

    monkeypatch.setattr("firewatch.sources.firms.FirmsClient", provider)
    result = CliRunner().invoke(
        app, ["fetch-firms", "--product", "VIIRS_NOAA20_SP", "--start", "2022-09-07"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["row_count"] == 1 and data["normalized"] is False
    assert KEY not in result.output
    settings.firms_map_key = None
    result = CliRunner().invoke(
        app, ["fetch-firms", "--product", "VIIRS_NOAA20_SP", "--start", "2022-09-07"]
    )
    assert result.exit_code == 1 and "FIRMS_MAP_KEY is missing" in result.output


def test_local_reservations_limit_successive_fetches_across_instances(tmp_path):
    areas = []

    def area(req):
        areas.append(req)
        return httpx.Response(200, text=HEADER)

    for _ in range(3):
        with client(tmp_path, routing(area)) as api:
            api.fetch(request())
    with client(tmp_path, routing(area)) as api, pytest.raises(FirmsError) as error:
        api.fetch(request())
    assert error.value.code == "RATE_LIMITED" and len(areas) == 3


def test_storage_failure_does_not_trigger_provider_retries(tmp_path, monkeypatch):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=QUOTA)

    def unavailable(path, data):
        raise PermissionError("Synthetic local file error")

    with client(tmp_path, handler) as api:
        monkeypatch.setattr(Path, "write_bytes", unavailable)
        with pytest.raises(FirmsError) as error:
            api.fetch(request())
    assert error.value.code == "STORAGE" and len(calls) == 1


def test_compressed_or_mislabeled_html_responses_never_become_success(tmp_path):
    with (
        client(
            tmp_path,
            routing(
                lambda req: httpx.Response(
                    200, content=(HEADER + ROW).encode(), headers={"content-type": "text/html"}
                )
            ),
            max_attempts=1,
        ) as api,
        pytest.raises(FirmsError) as error,
    ):
        api.fetch(request())
    assert error.value.code == "PROVIDER_ERROR"


def test_client_initialization_and_nested_registration_restore_log_factory(tmp_path, monkeypatch):
    before = logging.getLogRecordFactory()

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic initialization failure")

    with monkeypatch.context() as patch:
        patch.setattr(httpx, "Client", fail)
        with pytest.raises(RuntimeError):
            FirmsClient(SecretStr(KEY), tmp_path)
    assert logging.getLogRecordFactory() is before
    first, second = client(tmp_path, routing()), client(tmp_path, routing())
    first.close()
    assert logging.getLogRecordFactory() is not before
    second.close()
    assert logging.getLogRecordFactory() is before
