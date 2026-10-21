# Phase 3.2 acceptance: fixed climate context

- [x] NASA POWER T2M/PRECTOTCORR only, fixed HTTPS host and explicit UTC daily API.
- [x] Fixed 2010–2019 reference precedes the earliest planned 2022 snapshot;
  validated configuration requires 10–30 complete reference years.
- [x] Unique coarse climate cells and bounded annual requests, with reusable annual
  checkpoints, offline lookup, shared request budgets and no per-hotspot fetching.
- [x] Provider-declared fill, units, time standard, date bounds and point metadata
  validated; missing/invalid values stay null before computation.
- [x] Monthly mean daily temperature and precipitation computed independently from
  valid daily samples, with expected/valid day counts, contributing-year counts and
  a documented minimum coverage threshold. Leap days receive ordinary daily weight.
- [x] Temperature departure uses the preceding hourly weather value minus the
  monthly mean daily climate reference; it is explicitly not an hourly z-score.
- [x] Rainfall deficit is floored at zero and requires a complete preceding-day
  hourly series. Missing weather is never interpreted as zero rainfall.
- [x] UTC daily convention, reference years, coarse resolution, definitions, units,
  version, source hashes and sample counts are preserved in immutable manifests.
- [x] Future reference periods, wrong-month/cell baselines and later weather inputs
  cannot silently influence an earlier cutoff.
- [x] Missing baseline/variable values remain null with explicit quality flags.

## Real data verification

Ten complete POWER annual chunks (2010–2019) produced twelve monthly baselines
for one northern California climate cell. The original provider JSON and derived
manifest are preserved under ignored data/. The July reference has 310 valid daily
samples per variable, mean daily temperature 25.4730967742 C and mean daily
precipitation 0.0290322581 mm/day. Offline baseline reads are identical.

Context calculation was verified against the real cached Phase 3.1 hourly weather.
See [sanitized evidence](phase-3.2-smoke.json). The reference is not a WMO 30-year
normal, and neither one cell nor this check claims region-wide coverage or model
training readiness. The study region remains disabled; no pipeline Run was created.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_bootstrap.py
.\.venv\Scripts\ruff.exe check src tests migrations
.\.venv\Scripts\ruff.exe format --check src tests migrations
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -B scripts/check_scaffold.py
.\.venv\Scripts\python.exe -B scripts/check_locks.py
.\.venv\Scripts\python.exe -B -m build --no-isolation
```

Final verification: **548 tests passed**, including **64 new climate tests**
(57 unit and 7 PostgreSQL integration tests). Normal API/health/auth HTTP smoke,
real offline CLI, Ruff lint/format, strict mypy (65 source files), scaffold/link/Git
exclusions, dependency locks, source/wheel builds, packaged module checks, configured
secret scan and Git diff checks passed. Results are recorded in the smoke evidence.
The existing Starlette/httpx deprecation warning remains nonblocking.
Hosted CI, team review and automatic publication are not claimed. Shared API DTOs,
database migrations and dependencies are unchanged. Phase 3.3 remains pending.

See [operator and integration guide](climate-baselines.md).
