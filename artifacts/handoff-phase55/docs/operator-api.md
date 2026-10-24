# Operator API: Phase 1.5

The three operator POST endpoints admit durable jobs. All require one
`Authorization: Bearer <server-operator-token>` header, one `Idempotency-Key`
header, and an `application/json` object body. Acceptance is HTTP 202 with a
typed `JobAccepted` response, a `Location` pointing to the authenticated job GET,
and `Idempotency-Replayed: true|false`. OpenAPI now exposes 18 operations.

These handlers commit work to PostgreSQL; they do not call providers, run ML,
publish results, or sign/send blockchain transactions. The dedicated executor,
leases and retry processing remain Phase 3.5/Module 5 work. Until those workers
exist, accepted jobs remain queued; a 202 is not a completed refresh/replay/anchor.

## Authentication and read policy

The server requires a generated URL-safe operator token of 32-256 characters.
Native PostgreSQL setup already creates one using Python's cryptographic secret
generator in the ignored protected `.env`; existing secrets are preserved.
Length/format validation cannot prove randomness: provision random tokens rather
than memorable text, and rotate through trusted server configuration.
Constant-time comparisons protect bearer checks. Duplicate Authorization headers,
wrong schemes, missing and wrong tokens are rejected with 401. Mutating `/api/v1`
requests authenticate before JSON parsing or database access.

`GET /api/v1/jobs/{job_id}` always needs operator authentication. Other business
GETs follow `PUBLIC_READS_ENABLED`: development/test retain the documented public
local-prototype default; production defaults to private if the setting is absent.
Explicit `true` opts into public sanitized reads. The example environment is a
development configuration and explicitly sets true; choose this policy deliberately
when adapting it for production. Health checks stay public.

Operator actions are server-side tools. Never bundle a token into frontend code,
browser storage, sample requests committed with real values, or query parameters.
An interactive operator may use Swagger locally; a browser product needs a later
real login system before exposing operator actions. The API adds no training,
model-promotion or archive-import route; these remain trusted CLI responsibilities
in their owning later modules.

## Refresh admission

`POST /api/v1/jobs/refresh` requires `region_id`, `window_start`, `window_end`.
Times must be aware ISO timestamps, normalize to UTC, and have start < end.
The region must exist, be explicitly enabled in the seeded region allowlist, and
fit the area bound. Arbitrary coordinates, URLs, products or scorer overrides
are forbidden extra fields. The saved job freezes the region, live mode, cutoff,
window and configured scorer/feature versions.

Default bounds: window at most 48 hours, start no older than 72 hours before
admission, end no later than the current server time. This permits an overlapping
48-hour ingestion request; source access/coverage and execution are later work.
The seeded illustrative demo region stays disabled until coverage is verified.

Server-side PowerShell example, after privately provisioning the token in the
process environment and replacing the region UUID with a verified enabled region:

```powershell
$end = [DateTime]::UtcNow
$body = @{
    region_id = '<enabled-region-uuid>'
    window_start = $end.AddHours(-48).ToString('o')
    window_end = $end.ToString('o')
} | ConvertTo-Json
$headers = @{
    Authorization = "Bearer $env:ADMIN_API_TOKEN"
    'Idempotency-Key' = [Guid]::NewGuid().ToString()
}
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/jobs/refresh `
    -Headers $headers -ContentType application/json -Body $body
```

Keep the same key and body when retrying this submission. The server's `.env`
does not automatically populate another PowerShell process's environment.

## Historical replay allowlist

`POST /api/v1/jobs/replay` accepts only `{"scenario_id":"<allowlisted-slug>"}`.
The server-controlled `REPLAYS_CONFIG` defaults to `configs/replays.yaml`.
It intentionally contains an empty `replays-v1` scenario list until real archive
assets and coverage are prepared. Unknown scenario IDs are 404; clients cannot
submit paths, assets, dates, regions, models or download URLs to override a scenario.

Each trusted scenario has:

- `id`: unique lowercase slug, maximum 100 characters; `name`: 1-200 characters.
- `region_name`: name of a seeded enabled region within the area bound.
- `window_start`, `window_end`: fixed ordered aware timestamps; end is the replay
  cutoff and cannot exceed admission time. Default maximum duration is 31 days.
- `scorer_version`, `feature_version`: fixed, nonempty version identifiers.
- `asset_ids`: 1-200 distinct imported `source_assets` UUIDs.

The entire YAML is capped at 256 KiB and 100 scenarios and parsed with safe YAML
loading and strict DTOs. Every referenced asset must exist at admission. The
durable job freezes the scenario's metadata, asset IDs and versions. The worker
must verify actual files/checksums, temporal and regional coverage, and model
compatibility before execution; admission does not certify these properties or
download anything. Replay execution must use frozen inputs and historical weather,
never silently substitute current/live assets.

The read-side `/api/v1/replays` still lists immutable completed prepared runs,
not newly queued scenarios. The executor should record `replay_scenario.id/name`
in the eventual run manifest as described in the frontend handoff.

## Optional anchor admission

`POST /api/v1/alerts/{alert_id}/anchor` requires an empty `{}` body. The chain,
contract and digest come exclusively from server configuration and saved evidence.
`CHAIN_ENABLED=false` returns 503. When enabled, the alert and completed run must
exist and SHA-256 must match the original saved bytes. Bad local integrity is 409.

One transaction saves both the anchor job and its outbox row. Job payloads contain
IDs, the digest, chain ID and contract; no RPC credential or private key is stored
in them. Network identity is `eip155:<chain-id>:<lowercase-contract-address>`.
A second key for an existing alert/network returns 409 instead of creating another
transaction. Existing receipt records are also checked case-insensitively by
contract address. Legacy unscoped outbox records (such as `local`) conservatively
block new scheduling until a trusted operator resolves them; the API cannot guess
which chain transaction they represent. Failed outbox retry belongs to the worker,
not a new operator submission.

Retries with the original key recover the original job even if optional chaining
has since been disabled. The executor must recheck operational chain policy before
signing. This API never signs, sends RPC requests or declares an anchor confirmed.
The verification GET remains read-only and continues to report pending/unavailable
states honestly.

## Idempotency and bounded concurrency

Keys are 8-128 ASCII characters from letters, digits, `.`, `_`, `:`, `-`, starting
with a letter or digit. Exactly one header is required. Keys are globally scoped
to the operator API, stored as `operator:<key>`, and retained with the durable job.
Reuse across routes or anchor targets with different input is 409.

SHA-256 covers the operation plus validated canonical request identity. Equivalent
UTC offsets and JSON ordering normalize; changes to a refresh window or replay ID
conflict. The digest is a request identity, not a signature or hash of the enriched
worker payload. Server configuration is frozen on first admission. An identical
retry retrieves that job before rechecking capacity, region enablement, current
scenario configuration or chain settings; its returned status can reflect progress.
Failed validation/admission rolls back and does not reserve the key.

Admission owns a short database transaction with a PostgreSQL transaction-level
advisory lock (`repositories.jobs.ENQUEUE_LOCK`), serializing the idempotency lookup,
capacity check, and inserts across API processes. Lock waits default to 3 seconds;
failure returns a retryable 503. The default cap is 100 queued + running jobs across
all job types; succeeded/failed jobs do not consume admission capacity. A full queue
returns retryable `503 QUEUE_FULL`; existing admitted requests still replay.
Future scheduler/CLI producers must use the same lock/capacity protocol for new
admissions. Trusted SQL can bypass application policy, so it is not a public surface.

## Request and configuration bounds

Bodies default to a 16 KiB actual-byte cap and a 5-second receive timeout. Oversized
declared or streamed bodies return 413. Missing/false Content-Length cannot bypass
the actual-byte cap; duplicate, malformed or mismatched lengths are rejected.
Compressed bodies and non-JSON content types are 415. Invalid/ambiguous JSON,
duplicate object members, non-finite constants, non-object roots, unknown request
fields and missing/invalid idempotency keys are 422. Slow requests return 408.

Configuration names and defaults are explicit in `.env.example`:
`MAX_REQUEST_BODY_BYTES`, `REQUEST_BODY_TIMEOUT_SECONDS`, `MAX_JOB_QUEUE_DEPTH`,
`QUEUE_LOCK_TIMEOUT_MS`, `MAX_REFRESH_WINDOW_HOURS`, `MAX_REFRESH_AGE_HOURS`,
`MAX_REPLAY_WINDOW_DAYS`, `MAX_REGION_AREA_DEGREES2`, `REPLAYS_CONFIG`.
Each setting has an upper/lower validation bound. Area uses bbox degrees squared
as a request-cost bound, not a physical fire-area measurement.

## CORS, logging and operational boundaries

CORS origins remain an explicit allowlist, default empty, with no credentialed
cookies. Approved origins may preflight GET/POST with Content-Type, Authorization
and Idempotency-Key. Responses expose X-Request-ID, Location and Idempotency-Replayed.
CORS is a browser policy; it never replaces operator authentication.

API lifespan installs process-wide log-record redaction before dependency use,
including third-party logs, exception text and handlers created later. It masks
configured secret values and URL-encoded forms, bearer/basic credentials, HTTP and
PostgreSQL URLs, FIRMS credential-bearing paths, password/private-key assignments,
and normalized chain private keys. Uvicorn access records omit the entire request
target/query while retaining client, method and status. Handlers never log request
bodies, authorization headers, idempotency keys, provider URLs or worker payloads.
The previous log factory is restored after the last app closes, including failed
startup. Errors use the generic structured envelope and correlation ID.
Future modules must retain these logging rules and avoid printing secrets directly.

Local operation remains native PostgreSQL with free local storage; this phase
adds no dependencies, migrations, Docker Desktop, paid service or provider calls.
HTTPS deployment and the dedicated workers are addressed in their later phases.

Implementation references: [PostgreSQL advisory locks](https://www.postgresql.org/docs/16/explicit-locking.html#ADVISORY-LOCKS)
and [Python log-record factories](https://docs.python.org/3.12/library/logging.html#logging.setLogRecordFactory).

## Phase 5.4 anchor execution

Anchor admission and refresh/replay admission now use independent queue-capacity
buckets, each bounded by `MAX_JOB_QUEUE_DEPTH`. A chain backlog cannot stop
pipeline admission or scheduled refreshes. The dedicated `firewatch anchor-worker`
processes the outbox and synchronizes associated anchor-job progress. An operator
can inspect safe details with `anchor-status --job-id UUID`; signed transaction
bytes, payloads and private keys remain private. No GET submits chain work.
See [delivery, retry and recovery](durable-anchoring.md).
