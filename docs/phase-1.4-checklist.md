# Phase 1.4 acceptance

Implemented: all 15 read operations from the plan, typed api-v1 responses, stable
operation IDs, structured errors and generated OpenAPI export in CI.

- [x] Database/artifact readiness is independent of dependency-free liveness.
- [x] Region, source status, run/history, replay and scorer metadata are readable.
- [x] Ranking uses descending score then ascending snapshot UUID.
- [x] Pagination defaults to 50, caps at 200; GeoJSON caps at 2,000 and signals truncation.
- [x] Ranking, summary and GeoJSON share run/priority/bbox SQL selection.
- [x] Run/mode/cutoff/completeness/freshness travel with run-bound responses.
- [x] Empty completed runs return valid empty results; unknown IDs are 404,
  invalid filters 422, unavailable dependencies/incomplete outputs 503.
- [x] Replays never fall back to current/live outputs or current global weather.
- [x] Detail responses include features, timestamps, explanation and saved evidence.
- [x] Job details always require the configured operator bearer token.
- [x] Evidence hashing uses original bytes; GET verification performs no writes.
- [x] Stored receipts cannot falsely produce a verified chain match.
- [x] Public/private read policy, errors, units, null semantics and producer metadata
  are documented in `frontend-handoff.md`; no operator secret enters OpenAPI.
- [x] GitHub Actions exports the actual OpenAPI document as `firewatch-openapi`.

Acceptance tests exercise native PostgreSQL migrations and disposable schemas,
cross-run requests, filters/count consistency, a 2,001-snapshot cap/tie-order
fixture, real read-only database enforcement, failed source retries, valid empty
fetches, operator authentication, saved evidence verification, readiness failures,
artifact corruption and credential-safe errors. Fixtures are synthetic and isolated
from application data. Unit tests also run without PostgreSQL.

Reproduce Windows validation:

```powershell
.\.venv\Scripts\python.exe scripts/native_postgres.py start
.\.venv\Scripts\python.exe scripts/verify_bootstrap.py
.\.venv\Scripts\ruff.exe check src tests migrations scripts/native_postgres.py scripts/verify_bootstrap.py scripts/check_locks.py scripts/export_openapi.py
.\.venv\Scripts\ruff.exe format --check src tests migrations scripts/native_postgres.py scripts/verify_bootstrap.py scripts/check_locks.py scripts/export_openapi.py
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe scripts/export_openapi.py
```

Local validation: 110 tests passed against native PostgreSQL, including HTTP smoke checks for liveness, readiness, regions, status, Swagger and OpenAPI. Ruff, mypy, scaffold/lock checks, dependency checks and wheel/source builds passed.

No new dependencies, migration, paid service or Docker Desktop is required.
Native PostgreSQL and existing protected `.env` remain the local runtime.
Pipeline ingestion, scorer execution, prepared real-data replays, and real chain
RPC integration belong to later modules. No real fire-data coverage, trained-model
accuracy, production throughput or frontend/team sign-off is claimed here.
