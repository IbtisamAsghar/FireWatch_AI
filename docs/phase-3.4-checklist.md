# Phase 3.4 acceptance: feature schema v1

- [x] All seventeen planned values implemented with units, finite/range validation
  and explicit numeric-versus-missing semantics.
- [x] Fourteen nullable inputs have missing indicators; unknown confidence/daynight,
  missing brightness/FRP, incomplete rainfall and missing baselines are not invented zeros.
- [x] The model input helper admits exactly 31 ordered inputs: 17 values plus 14
  booleans. IDs, coordinates, labels, scores and status metadata are excluded.
- [x] Feature records persist cutoff/version, units, source asset IDs/hashes,
  selected revisions, enrichment pins, quality flags and integrity hash.
- [x] One shared typed pure builder supports training/replay/live; features-v1
  rejects a changed spatial definition mislabeled under its existing version.
- [x] Strict time/radius boundaries and later source/correction/weather/baseline
  arrivals cannot change already saved snapshots or feature records.
- [x] Atomic persistence, concurrent retries, rollback, missing-input pinning,
  explicit enrichment forks and tamper rejection have PostgreSQL coverage.

See [definitions/operator guide](features-v1.md) and
[machine-readable schema](features-v1.schema.json).

## Verification

**635 tests passed**, including **48 new feature tests** (37 unit and 11 PostgreSQL
integration tests). HTTP/health/auth smoke, Ruff lint/format, strict mypy (67 source
files), scaffold/link/Git exclusions, dependency locks, source/wheel builds, isolated
runtime wheel feature/CLI smoke, pip check, configured-secret scanning and Git diff
checks passed. The existing Starlette/httpx warning remains nonblocking.

The real July 2024 replay Run produced six feature sets from its frozen FIRMS
snapshots. All six weather cells (144 hourly records) and four climate cells were
prepared with real provider data; no feature set lacked weather or climate context.
Repeated cached computation returned identical hashes. Table counts remained six
snapshots, 832 memberships and six feature sets. The region remains disabled; the
Run remains incomplete and latest live is unchanged. See
[sanitized real-data evidence](phase-3.4-smoke.json).

Real source inputs remain under ignored data/. No model labels/scores, worker scheduling or
completed pipeline publication is introduced by this phase. Hosted CI and team
approval are not claimed. Phase 3.5 remains pending.

Reproduce from the repository root:

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_bootstrap.py
.\.venv\Scripts\ruff.exe check src tests migrations
.\.venv\Scripts\ruff.exe format --check src tests migrations
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -B scripts/check_scaffold.py
.\.venv\Scripts\python.exe -B scripts/check_locks.py
.\.venv\Scripts\python.exe -B -m build --no-isolation
```
