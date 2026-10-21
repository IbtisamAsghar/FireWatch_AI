# FIRMS Area client (Phase 2.2)

`firewatch.sources.firms.FirmsClient` fetches bounded raw NOAA-20/NOAA-21
VIIRS CSV batches. It saves source evidence before inspecting CSV structure and
returns `FirmsBatch` (`firms-raw-batch-v1`) with `SourceAssetInput` metadata.
The normalizer and `DetectionBatch` service boundary remain Phase 2.3 work.

## CLI and Python use

From the repository root, using the MAP_KEY already in the ignored local `.env`:

```powershell
.\.venv\Scripts\firewatch.exe fetch-firms `
  --region northern-california --product VIIRS_NOAA20_SP `
  --start 2022-09-07 --days 1
```

The command prints the batch ID, product, retrieval time, row count, raw path,
checksum and manifest path. It never prints the key or raw rows. It does not
connect to the application database or enable a configured region. The optional
`--output` changes the evidence root; all cooperating callers using one key must
share that root to share local quota accounting. Default: `RAW_DATA_DIR/firms`.
Keep the root under ignored private data storage, not under source control.

```python
from firewatch.config import get_settings
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.firms import FirmsAreaRequest, FirmsClient

settings = get_settings()
if settings.firms_map_key is None:
    raise RuntimeError("Configure the local FIRMS_MAP_KEY")
request = FirmsAreaRequest(
    product="VIIRS_NOAA20_SP",
    bbox=BoundingBox(west=-122.5, south=38.5, east=-120, north=40.5),
    start_date="2022-09-07", days=1,
)
with FirmsClient(settings.firms_map_key, settings.raw_data_dir / "firms") as client:
    batch = client.fetch(request)
```

Use the client as a context manager or call `close()` so connections and log
redaction registrations are released. Standalone clients register their own
credential redaction; they do not require database/operator secrets. The CLI also
keeps the existing application secret redaction active.

## Requests and bounds

The only destination is `https://firms.modaps.eosdis.nasa.gov`, using the Area
and map-key status endpoints. No caller-provided base URL is accepted. Redirects
are rejected, TLS verification stays enabled, and environment proxy settings are
not inherited. The injected HTTP transport is a test seam, not user input.

Requests allow only the four existing `Product` enum values: NOAA-20 and NOAA-21,
each NRT or SP. The bbox must be finite, ordered, within global coordinate bounds,
and at most 100 square degrees. Each request covers 1–5 UTC calendar days,
inclusive from `start_date`; no future dates or pre-sensor dates are accepted.
Inputs are revalidated at fetch time, including nested models. Current available
dates are provider-specific; use the Phase 2.1 availability evidence/probe and
handle unavailable dates explicitly. No product or sensor is substituted.

## Timeouts, retries and quota

Default policy: connect timeout 5 seconds, read inactivity timeout 20 seconds,
three attempts, and a 90-second operation deadline checked between response
chunks and attempts. One pending blocking read can finish after that deadline,
within its inactivity timeout. Response bodies are capped at 8 MiB and CSV rows
at 50,000. Do not use this client to download whole seasonal archives.

Transient transport failures, HTTP 408/425/500/502/503/504, HTML/provider error
pages and malformed CSV receive bounded retries. Backoff doubles from one second,
caps at eight seconds, and adds uniform 0–1 second jitter by default.
Authentication failures, redirects, resource bounds, credential echoes and invalid
quota schemas fail immediately.

Both numeric and HTTP-date `Retry-After` values are honored. A retry never sleeps
less than the header delay. HTTP 429 without a usable header and textual transaction
limit errors defer for 600 seconds. A delay above 30 seconds or the remaining
operation budget returns a retryable `FirmsError` instead of sleeping indefinitely.
The caller/worker should defer until `retry_after_seconds` has elapsed.

NASA documents a default 5,000-transaction budget per 10 minutes; one HTTP request
can consume several transactions. This client uses an intentionally conservative
local sliding-window budget of 4,000 units, reserving 100 units before each quota
HTTP attempt and 1,000 before each Area HTTP attempt, including failed attempts.
These are estimates, **not NASA's actual charging formula**. A successful fetch
normally reserves 1,100 units. Every Area attempt first checks the provider's
current usage/limit and refuses admission without at least 1,100 units of headroom.

Reservations and cooldowns are serialized with SQLite transactions in the shared
evidence root's `.quota.sqlite3`. Only a SHA-256 key fingerprint is stored. State
survives client/process restarts; failed requests are not refunded. Other hosts
and tools sharing the key are not coordinated by this local ledger; the remote
status preflight and 429 handling provide additional safeguards. A provider status
snapshot cannot guarantee the usage remains unchanged before another process calls.

## Evidence and failure semantics

Every fetch creates a fresh UUID directory. `manifest.json` is written atomically
and contains validated parameters, a credential-placeholder endpoint, retrieval
times, HTTP status, completeness, conservative reservations, raw file hashes,
attempt count, error codes and final `succeeded`, `empty`, or `failed` state.
Manifest and raw files are local artifacts; no database publication occurs.

Each received bounded identity-encoded response body is saved unchanged as
`response-NN.bin`, including quota replies and malformed/error responses, before
CSV parsing. The Area body's `SourceAssetInput.format` is `csv` despite the generic
raw-file extension. Available partial transfer bytes are retained and marked
incomplete. Unsupported content encodings fail; responses echoing the credential
are discarded and explicitly marked, with no raw credential-bearing file saved.

CSV inspection requires the documented VIIRS columns, unique headers, consistent
row widths and valid UTF-8 (optional BOM). Unknown extra columns are retained.
A header-only response is a valid empty batch. An empty body, HTML, provider error,
malformed schema or interrupted transfer is never reported as zero detections.
Cell strings (including `0037` and raw confidence categories) remain unchanged;
measurement, timestamp, sensor and coordinate conversion is Phase 2.3's job.
Observed processing versions are recorded; an empty/mixed batch does not invent
a unique observed version. An empty batch does not prove absence of fire activity.

`FirmsError` exposes a fixed `code`, safe message, `retryable`, optional
`retry_after_seconds`, and a manifest path when available. No provider URL,
credential or raw error text enters its message. Storage errors stop fetching.

## Verification and sources

See [Phase 2.2 acceptance](phase-2.2-checklist.md) and the
[real one-day smoke evidence](phase-2.2-smoke.json). Raw downloaded detections
remain ignored; committed smoke evidence contains metadata/checksums only.

Official references checked 21 October 2026:
[NASA Area API](https://firms.modaps.eosdis.nasa.gov/api/area/),
[NASA API tutorial and quota endpoint](https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html),
[NASA MAP_KEY limits](https://firms.modaps.eosdis.nasa.gov/api/map_key/).
