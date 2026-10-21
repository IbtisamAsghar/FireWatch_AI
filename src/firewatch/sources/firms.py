"""Bounded FIRMS Area client: immutable raw evidence before normalization."""

import csv
import hashlib
import io
import json
import random
import re
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from uuid import UUID, uuid4

import httpx
from pydantic import BeforeValidator, ConfigDict, Field, SecretStr, model_validator

from firewatch.logging import install_secret_redaction
from firewatch.schemas.common import DTO, Product, Provider, UTCDateTime
from firewatch.schemas.contracts import BoundingBox, SourceAssetInput

HOST = "https://firms.modaps.eosdis.nasa.gov"
ATTRIBUTION = "https://www.earthdata.nasa.gov/data/tools/firms"
REQUIRED_COLUMNS = {
    "latitude",
    "longitude",
    "bright_ti4",
    "scan",
    "track",
    "acq_date",
    "acq_time",
    "satellite",
    "instrument",
    "confidence",
    "version",
    "bright_ti5",
    "frp",
    "daynight",
}
MESSAGES = {
    "AUTHENTICATION": "FIRMS rejected the configured MAP_KEY.",
    "HTTP_ERROR": "FIRMS returned an unsuccessful HTTP status.",
    "REDIRECT": "FIRMS redirects are not permitted.",
    "NETWORK": "FIRMS connection or response transfer failed.",
    "DEADLINE": "The FIRMS operation exceeded its time budget.",
    "RATE_LIMITED": "FIRMS quota is unavailable; defer this request.",
    "PROVIDER_ERROR": "FIRMS returned a provider error instead of CSV.",
    "MALFORMED_CSV": "FIRMS returned malformed or unsupported CSV.",
    "RESPONSE_LIMIT": "The FIRMS response exceeded the configured data bound.",
    "CREDENTIAL_ECHO": "A credential-bearing provider response was discarded.",
    "STATUS_SCHEMA": "FIRMS quota status could not be validated.",
    "STORAGE": "FIRMS evidence or quota storage is unavailable.",
    "CLOSED": "The FIRMS client is closed.",
}


class FirmsError(RuntimeError):
    """Fixed diagnostics only; never retain HTTP requests, URLs or provider bodies."""

    def __init__(
        self, code: str, *, retryable: bool = False, retry_after_seconds: float | None = None
    ) -> None:
        super().__init__(MESSAGES[code])
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.manifest_path: Path | None = None


def date_input(value: object) -> object:
    if isinstance(value, datetime) or not isinstance(value, date | str):
        raise ValueError("Use a calendar date in YYYY-MM-DD format")
    if isinstance(value, str) and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Use a calendar date in YYYY-MM-DD format")
    return value


class FirmsAreaRequest(DTO):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    product: Product
    bbox: BoundingBox
    start_date: Annotated[date, BeforeValidator(date_input)]
    days: int = Field(default=1, ge=1, le=5, strict=True)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (self.bbox.east - self.bbox.west) * (self.bbox.north - self.bbox.south) > 100:
            raise ValueError("Area requests are limited to 100 square degrees")
        earliest = date(2018, 4, 1) if "NOAA20" in self.product else date(2023, 3, 1)
        try:
            self.start_date + timedelta(days=self.days - 1)
        except OverflowError:
            raise ValueError("Date window exceeds the supported calendar") from None
        if not earliest <= self.start_date:
            raise ValueError("Date is outside the selected sensor's lifetime")
        return self


class FirmsPolicy(DTO):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)
    max_attempts: int = Field(default=3, ge=1, le=5)
    connect_timeout: float = Field(default=5, gt=0, le=30)
    read_timeout: float = Field(default=20, gt=0, le=60)
    operation_seconds: float = Field(default=90, gt=0, le=300)
    max_retry_wait: float = Field(default=30, ge=0, le=60)
    backoff_seconds: float = Field(default=1, gt=0, le=10)
    max_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    max_rows: int = Field(default=50000, ge=1, le=100000)
    # Conservative reservations, not an assertion of NASA's actual charging formula.
    window_units: int = Field(default=4000, ge=1100, le=4500)


class FirmsBatch(DTO):
    """Pre-normalization handoff; not a normalized DetectionBatch or a DB write."""

    schema_version: Literal["firms-raw-batch-v1"] = "firms-raw-batch-v1"
    batch_id: UUID
    request: FirmsAreaRequest
    retrieved_at: UTCDateTime
    asset: SourceAssetInput
    columns: list[str]
    rows: list[dict[str, str]]
    row_count: int
    empty: bool
    processing_versions: list[str]
    manifest_path: Path


class QuotaLedger:
    """SQLite reservations/cooldowns coordinate cooperating processes on one host."""

    def __init__(self, path: Path, key_id: str, limit: int) -> None:
        self.path, self.key_id, self.limit = path, key_id, limit

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.execute("CREATE TABLE IF NOT EXISTS usage (key_id TEXT, at REAL, units INT)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS cooldown (key_id TEXT PRIMARY KEY, until_time REAL)"
        )
        return connection

    def reserve(self, units: int, now: float) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM usage WHERE at <= ?", (now - 600,))
            blocked = connection.execute(
                "SELECT until_time FROM cooldown WHERE key_id=?", (self.key_id,)
            ).fetchone()
            if blocked and blocked[0] > now:
                raise FirmsError(
                    "RATE_LIMITED", retryable=True, retry_after_seconds=blocked[0] - now
                )
            used, oldest = connection.execute(
                "SELECT COALESCE(SUM(units),0), MIN(at) FROM usage WHERE key_id=?",
                (self.key_id,),
            ).fetchone()
            if used + units > self.limit:
                raise FirmsError(
                    "RATE_LIMITED",
                    retryable=True,
                    retry_after_seconds=max(1, (oldest or now) + 600 - now),
                )
            connection.execute("INSERT INTO usage VALUES (?,?,?)", (self.key_id, now, units))
            connection.commit()

    def defer(self, until: float) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "INSERT INTO cooldown VALUES (?,?) ON CONFLICT(key_id) DO UPDATE "
                "SET until_time=MAX(until_time,excluded.until_time)",
                (self.key_id, until),
            )


def retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    if re.fullmatch(r"[0-9]+", value.strip()):
        # Limit conversion work on an untrusted header; huge values mean defer.
        return float(min(int(value.strip()[:10]), 31536000))
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return None
        return max(0, (parsed - now).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


@dataclass
class _Response:
    body: bytes
    status: int | None = None
    media_type: str = ""
    retry_after: float | None = None
    error: str | None = None
    complete: bool = False


class FirmsClient:
    """Owns a fixed-host HTTP client and secret redaction for its whole lifetime."""

    def __init__(
        self,
        key: SecretStr,
        output_dir: Path = Path("data/raw/firms"),
        *,
        policy: FirmsPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        token = key.get_secret_value()
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", token) or token.upper().startswith(
            ("REPLACE_", "CHANGE_ME")
        ):
            raise ValueError("Configure a valid local FIRMS_MAP_KEY")
        self._key = key
        self.policy = FirmsPolicy.model_validate((policy or FirmsPolicy()).model_dump())
        self.output_dir = output_dir.resolve()
        self._sleep, self._now, self._monotonic, self._jitter = sleep, now, monotonic, jitter
        self._closed = False
        self._redaction = install_secret_redaction((token,))
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.ledger = QuotaLedger(
                self.output_dir / ".quota.sqlite3",
                hashlib.sha256(token.encode()).hexdigest(),
                self.policy.window_units,
            )
            self._http = httpx.Client(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                timeout=httpx.Timeout(
                    self.policy.read_timeout, connect=self.policy.connect_timeout
                ),
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
                headers={"Accept-Encoding": "identity", "User-Agent": "FireWatch-AI/0.1 FIRMS"},
            )
        except Exception:
            self._redaction()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            try:
                self._http.close()
            finally:
                self._closed = True
                self._redaction()

    def _receive(self, url: str, deadline: float) -> _Response:
        result = _Response(b"")
        chunks = bytearray()
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            result.error = "DEADLINE"
            return result
        try:
            with self._http.stream(
                "GET",
                url,
                timeout=httpx.Timeout(
                    min(self.policy.read_timeout, remaining),
                    connect=min(self.policy.connect_timeout, remaining),
                ),
            ) as response:
                result.status = response.status_code
                result.media_type = (
                    response.headers.get("content-type", "").split(";", 1)[0].lower()
                )
                result.retry_after = retry_after(response.headers.get("retry-after"), self._now())
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    result.error = "PROVIDER_ERROR"
                    return result
                for chunk in response.iter_bytes():
                    if len(chunks) + len(chunk) > self.policy.max_bytes:
                        result.error = "RESPONSE_LIMIT"
                        break
                    chunks.extend(chunk)
                    if self._monotonic() >= deadline:
                        result.error = "DEADLINE"
                        break
                else:
                    result.complete = True
        except httpx.HTTPError:
            result.error = "NETWORK"
        result.body = bytes(chunks)
        return result

    @staticmethod
    def _write(path: Path, manifest: dict[str, Any]) -> None:
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        temp.replace(path)

    def _exchange(
        self,
        kind: Literal["quota", "area"],
        url: str,
        folder: Path,
        manifest: dict[str, Any],
        deadline: float,
    ) -> _Response:
        units = 100 if kind == "quota" else 1000
        self.ledger.reserve(units, self._now().timestamp())
        result = self._receive(url, deadline)
        item: dict[str, Any] = {
            "kind": kind,
            "retrieved_at": self._now().isoformat(),
            "http_status": result.status,
            "reserved_transaction_units": units,
            "complete": result.complete,
        }
        if self._key.get_secret_value().encode() in result.body:
            item["body_discarded"] = True
            result.body, result.error = b"", "CREDENTIAL_ECHO"
        else:
            filename = f"response-{len(manifest['responses']) + 1:02d}.bin"
            (folder / filename).write_bytes(result.body)
            item.update(
                filename=filename,
                sha256=hashlib.sha256(result.body).hexdigest(),
                byte_count=len(result.body),
            )
        manifest["responses"].append(item)
        self._write(folder / "manifest.json", manifest)
        if result.error:
            raise FirmsError(result.error, retryable=result.error in {"NETWORK", "DEADLINE"})
        if result.status == 429:
            raise FirmsError(
                "RATE_LIMITED",
                retryable=True,
                retry_after_seconds=result.retry_after if result.retry_after is not None else 600,
            )
        if result.status in {401, 403}:
            raise FirmsError("AUTHENTICATION")
        if result.status is not None and 300 <= result.status < 400:
            raise FirmsError("REDIRECT")
        if result.status != 200:
            raise FirmsError(
                "HTTP_ERROR",
                retryable=result.status in {408, 425, 500, 502, 503, 504},
                retry_after_seconds=result.retry_after,
            )
        prefix = result.body[:2048].lstrip().lower()
        if b"invalid" in prefix and (b"map_key" in prefix or b"map key" in prefix):
            raise FirmsError("AUTHENTICATION")
        if b"transaction" in prefix and any(x in prefix for x in (b"exceed", b"limit")):
            if kind != "quota" or not prefix.startswith(b"{"):
                raise FirmsError("RATE_LIMITED", retryable=True, retry_after_seconds=600)
        if result.media_type == "text/html" or prefix.startswith((b"<", b"error", b"invalid")):
            raise FirmsError("PROVIDER_ERROR", retryable=True)
        return result

    def _quota(self, folder: Path, manifest: dict[str, Any], deadline: float) -> None:
        response = self._exchange(
            "quota",
            f"{HOST}/mapserver/mapkey_status/?MAP_KEY={self._key.get_secret_value()}",
            folder,
            manifest,
            deadline,
        )
        try:
            data = json.loads(response.body)
            limit, used = data["transaction_limit"], data["current_transactions"]
            if (
                type(limit) is not int
                or type(used) is not int
                or limit <= 0
                or used < 0
                or data["transaction_interval"] != "10 minutes"
            ):
                raise ValueError("Invalid status")
        except (ValueError, KeyError, TypeError):
            raise FirmsError("STATUS_SCHEMA") from None
        manifest["provider_quota_before"] = {"limit": limit, "used": used}
        if used + 1100 > min(self.policy.window_units, limit):
            raise FirmsError("RATE_LIMITED", retryable=True, retry_after_seconds=600)

    def _csv(self, body: bytes) -> tuple[list[str], list[dict[str, str]]]:
        try:
            reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")), strict=True)
            fields = reader.fieldnames
            if (
                not fields
                or len(fields) != len(set(fields))
                or not REQUIRED_COLUMNS <= set(fields)
                or any(not x or len(x) > 100 for x in fields)
            ):
                raise ValueError("Invalid header")
            rows: list[dict[str, str]] = []
            for row in reader:
                if len(rows) >= self.policy.max_rows:
                    raise FirmsError("RESPONSE_LIMIT")
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Invalid row width")
                rows.append({str(key): str(value) for key, value in row.items()})
            return list(fields), rows
        except (UnicodeError, csv.Error, ValueError):
            raise FirmsError("MALFORMED_CSV", retryable=True) from None

    def fetch(self, request: FirmsAreaRequest) -> FirmsBatch:
        if self._closed:
            raise FirmsError("CLOSED")
        # Revalidate nested models even if a caller used model_construct/model_copy.
        request = FirmsAreaRequest.model_validate(request.model_dump(mode="json"))
        if request.start_date + timedelta(days=request.days - 1) > self._now().date():
            raise ValueError("FIRMS date windows must not extend into the future")
        identifier = uuid4()
        folder = self.output_dir / str(identifier)
        manifest: dict[str, Any] = {
            "schema_version": "firms-request-manifest-v1",
            "batch_id": str(identifier),
            "provider": "firms",
            "host": HOST,
            "endpoint": "/api/area/csv/{MAP_KEY}/{PRODUCT}/{BBOX}/{DAYS}/{DATE}",
            "request": request.model_dump(mode="json"),
            "responses": [],
            "status": "fetching",
            "attribution_reference": ATTRIBUTION,
            "transaction_accounting": "Estimated reservations; provider charges may differ.",
        }
        deadline = self._monotonic() + self.policy.operation_seconds
        try:
            folder.mkdir()
            self._write(folder / "manifest.json", manifest)
            for attempt in range(1, self.policy.max_attempts + 1):
                manifest["attempts"] = attempt
                try:
                    self._quota(folder, manifest, deadline)
                    box = request.bbox
                    coords = f"{box.west},{box.south},{box.east},{box.north}"
                    response = self._exchange(
                        "area",
                        f"{HOST}/api/area/csv/{self._key.get_secret_value()}/"
                        f"{request.product.value}/{coords}/{request.days}/{request.start_date}",
                        folder,
                        manifest,
                        deadline,
                    )
                    columns, rows = self._csv(response.body)
                    versions = sorted({row["version"] for row in rows if row["version"]})
                    raw = manifest["responses"][-1]
                    retrieved = self._now()
                    asset = SourceAssetInput(
                        provider=Provider.FIRMS,
                        product=request.product.value,
                        request_metadata=request.model_dump(mode="json"),
                        retrieved_at=retrieved,
                        storage_path=str(folder / raw["filename"]),
                        content_hash=raw["sha256"],
                        format="csv",
                        processing_version=versions[0]
                        if len(versions) == 1 and len(versions[0]) <= 100
                        else "not_unique_or_observed",
                    )
                    manifest.update(
                        status="empty" if not rows else "succeeded",
                        row_count=len(rows),
                        columns=columns,
                        processing_versions=versions,
                        retrieved_at=retrieved.isoformat(),
                        asset=asset.model_dump(mode="json"),
                    )
                    self._write(folder / "manifest.json", manifest)
                    return FirmsBatch(
                        batch_id=identifier,
                        request=request,
                        retrieved_at=retrieved,
                        asset=asset,
                        columns=columns,
                        rows=rows,
                        row_count=len(rows),
                        empty=not rows,
                        processing_versions=versions,
                        manifest_path=folder / "manifest.json",
                    )
                except FirmsError as error:
                    if error.retry_after_seconds is not None:
                        self.ledger.defer(self._now().timestamp() + error.retry_after_seconds)
                    remaining = deadline - self._monotonic()
                    delay = max(
                        min(8, self.policy.backoff_seconds * 2 ** (attempt - 1))
                        + self._jitter(0, self.policy.backoff_seconds),
                        error.retry_after_seconds or 0,
                    )
                    if (
                        not error.retryable
                        or attempt == self.policy.max_attempts
                        or delay > self.policy.max_retry_wait
                        or delay >= remaining
                    ):
                        raise
                    manifest.setdefault("retry_errors", []).append(error.code)
                    self._sleep(delay)
        except FirmsError as error:
            manifest.update(
                status="failed",
                error_code=error.code,
                retryable=error.retryable,
                retry_after_seconds=error.retry_after_seconds,
            )
            try:
                self._write(folder / "manifest.json", manifest)
            except OSError:
                raise FirmsError("STORAGE") from None
            error.manifest_path = folder / "manifest.json"
            raise
        except (OSError, sqlite3.Error):
            raise FirmsError("STORAGE") from None
        raise AssertionError("Bounded attempts always return or raise")
