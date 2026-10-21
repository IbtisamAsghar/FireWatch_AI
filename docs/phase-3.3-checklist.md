# Phase 3.3 acceptance: reproducible cluster snapshots

- [x] Aware UTC cutoff, `(T-6h,T]` candidates and `(T-24h,T]` scoped history.
- [x] Future and exact lower-bound observations excluded from processing and input hashes.
- [x] scikit-learn DBSCAN with haversine, eps=2/6371.0088 radians, min_samples=1;
  singleton behavior and bounded neighborhood allocation are explicit.
- [x] Deterministic input/member/output ordering, spherical centroids and run-derived
  UUIDs; repeated processing does not append duplicate rows.
- [x] FRP max/sum, latest observation, sensor/confidence counts and partial/null
  FRP quality are saved with member identities.
- [x] Independent centroid-radius activity counts for 6, 12 and 24 hours.
- [x] Run-specific clusters and frozen source revisions; no incident tracking claim.
- [x] Representative real connected, compact and singleton cases inspected; wide
  chained components flagged without changing the versioned radius.
- [x] One typed processor and fixed spatial/time configuration for live/train/replay.
- [x] Atomic persistence, source-day coverage, explicit empty stages, concurrent
  retries, manifest/materialized integrity and rollback covered by PostgreSQL tests.

## Real acceptance

The offline July 2024 source stage produced six clusters from 832 current candidates
and 1,731 past observations. Repeating with frozen inputs returned identical results;
table counts remained six snapshots and 832 memberships. Two clusters are wider than
the centroid radius because of DBSCAN chaining. The 687-member largest component
reaches 13.88 km from its centroid and has only 21 current observations within 2 km.
This limitation is documented; no independent fire/event count is claimed.

See [real evidence](phase-3.3-smoke.json) and [clustering guide](cluster-snapshots.md).
The disabled study region was not activated. The saved research Run is replay mode,
remains incomplete, and does not replace latest live results. Features, weather joins,
scores, evidence, worker execution and pipeline completion remain later phases.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv\Scripts\python.exe -B scripts/verify_bootstrap.py
.\.venv\Scripts\ruff.exe check src tests migrations
.\.venv\Scripts\ruff.exe format --check src tests migrations
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -B scripts/check_scaffold.py
.\.venv\Scripts\python.exe -B scripts/check_locks.py
.\.venv\Scripts\python.exe -B -m build --no-isolation
```

NumPy/scikit-learn and their dependencies are now in both universal SHA-256 locks.
Existing dependency pins were preserved. No migration or shared API DTO changed.
Final validation: **587 tests passed**, including **39 new clustering tests**
(31 unit and 8 PostgreSQL tests). HTTP/health/auth smoke, real offline stage repeats,
Ruff lint/format, strict mypy (66 source files), scaffold/link/Git exclusions, lock
consistency, existing-pin preservation, source/wheel builds, a fresh runtime-only
wheel installation and clustering/CLI smoke, pip check, configured-secret scan and
Git diff checks passed. Results are recorded in the smoke evidence. The existing
Starlette/httpx deprecation warning remains nonblocking. Hosted CI and team
review are not claimed; Phase 3.4 remains pending.
