# Phase 1.5 acceptance

- [x] Refresh, allowlisted replay and optional alert anchor POST endpoints queue
  durable jobs with typed 202 responses and stable OpenAPI operation IDs.
- [x] Every mutation and job-detail response requires a server operator bearer token;
  no secret is embedded in code, OpenAPI, browser artifacts or accepted job payloads.
- [x] Local public-read policy is explicit; production defaults private when unspecified.
- [x] Required idempotency keys return the original job on an identical retry and
  409 on conflicting input, including concurrent submissions and cross-route reuse.
- [x] Enabled-region and bbox-area allowlists, recent/live time windows, trusted
  fixed replay IDs/assets, actual streamed body size/time, and queue depth are bounded.
- [x] Anchor job/outbox creation is atomic; existing network/legacy work blocks duplicates.
- [x] Training, model promotion and archive imports have no HTTP route and remain
  trusted CLI responsibilities in their owning modules.
- [x] CORS uses explicit origins and no wildcard credentialed access; POST/header
  preflights work without bypassing authentication.
- [x] Authorization, provider credential paths, database credentials and private keys
  are redacted from application/third-party/exception/access log records.

Integration acceptance uses native PostgreSQL and fresh disposable schemas. It
tests persistence after returning 202, retries after configuration/capacity changes,
concurrent duplicate and distinct keys, queue saturation/release, bounded lock waits,
transaction rollback without orphan outbox rows, request validation and fixed replay
asset admission. Unit tests cover chunked/slow/disconnected bodies, misleading or
duplicate lengths, ambiguous JSON, CORS, token format, production defaults, OpenAPI
authentication, late logger handlers, exception redaction and startup cleanup.

Local verification: 154 tests passed, including the native PostgreSQL acceptance
suite. Ruff, formatting, mypy, scaffold/lock/dependency checks, source/wheel builds,
and real HTTP checks passed. OpenAPI exports all 18 operations without secrets.

Reproduce:

```powershell
.\.venv\Scripts\python.exe scripts/native_postgres.py start
.\.venv\Scripts\python.exe scripts/verify_bootstrap.py
.\.venv\Scripts\ruff.exe check src tests migrations scripts/native_postgres.py scripts/verify_bootstrap.py scripts/check_locks.py scripts/export_openapi.py
.\.venv\Scripts\ruff.exe format --check src tests migrations scripts/native_postgres.py scripts/verify_bootstrap.py scripts/check_locks.py scripts/export_openapi.py
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe scripts/export_openapi.py
```

All application writes commit before acceptance. Read APIs remain PostgreSQL
read-only transactions. Application schema stays revision `0002`. Tests do not seed
synthetic jobs, scenarios or fire results into the application database.

The committed replay allowlist is empty and the demo region stays disabled until
real source coverage/imports are prepared. Queue admission is complete; execution,
provider fetching, worker leases/retries, scoring and real blockchain sending remain
their planned later modules. No completed real refresh, replay or transaction is
claimed by this phase. See [operator API](operator-api.md) for exact contracts,
defaults, error semantics and remaining worker responsibilities.
