# Phase 3.1 acceptance: weather clients and caching

- [x] Separate live, historical-forecast and explicit reanalysis adapters share the
  existing typed weather DTO. Replay requests reject the live product.
- [x] GMT/UTC hourly timestamps, Celsius, m/s winds and mm precipitation are explicit;
  returned time and unit metadata are validated.
- [x] Historical requests use historical endpoints and matching valid dates; retrieval
  time and retrospective availability semantics remain separate.
- [x] Canonical 0.1-degree cell centers and grid version are documented; requested
  centers and returned provider coordinates are both stored.
- [x] PostgreSQL cache uses provider/product/model/cell/hour/source versions, immutable
  fetch revisions, retrieval cutoffs and live cache expiry. No migration is required.
- [x] Multi-location batches, synchronous requests, shared persistent atomic budgets,
  bounded retries/timeouts/response size and rate-limit cooldowns are implemented.
- [x] Cutoff selection excludes future hours and exposes the last completed interval.
- [x] Rolling rainfall requires all 24 preceding hourly sums; missing hours/values
  remain null, zero remains zero, and no local interpolation is performed.
- [x] Stale/missing/reanalysis flags are explicit. Native provider interpolation is
  marked unknown rather than asserted absent.

## Verification

A real two-cell GFS historical-forecast request for 24 July 2024 01:00 UTC through
25 July 2024 00:00 UTC returned 48 hourly records. Explicit returned units passed
validation, the preserved JSON was checksummed, and PostgreSQL persistence followed
raw-evidence revalidation. An offline cache read returned identical typed records.
The first cell had a complete preceding-day precipitation sum of 0 mm.
See [sanitized evidence](phase-3.1-smoke.json).

Regression coverage includes request bounds, grid boundaries, product/model/mode
isolation, UTC metadata and timestamps, malformed responses, units and shapes,
invalid/null values, missing hours, rainfall cutoffs, future exclusion, stale age,
HTTP retries, rate limits, shared/concurrent budgets, response size, CLI validation,
immutable revisions, offline reads, retrieval cutoffs, expiry, checksum/row tampering,
idempotent persistence and transaction rollback.

Final verification: **484 tests passed**, including **72 new weather tests**
(63 unit and 9 PostgreSQL integration tests), and normal API/health/auth HTTP smoke
passed. Ruff lint/format, strict mypy (64 source files), scaffold/link/Git exclusions,
dependency-lock consistency, source/wheel builds, wheel content and Git diff checks
passed. Configured-secret scanning passed. The existing Starlette/httpx deprecation
warning remains nonblocking. Results are recorded in the smoke evidence. Real provider data remains ignored under data/. The study
region remains disabled; no pipeline Run, climate baseline, feature, score or label
is created by this phase. Hosted CI and team approval are not claimed.

See [operator and integration guide](weather-cache.md). Phase 3.2 remains pending.


Reproduce from the repository root with the existing environment:

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_bootstrap.py
.\.venv\Scripts\ruff.exe check src tests migrations
.\.venv\Scripts\ruff.exe format --check src tests migrations
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -B scripts/check_scaffold.py
.\.venv\Scripts\python.exe -B scripts/check_locks.py
.\.venv\Scripts\python.exe -B -m build --no-isolation
```
