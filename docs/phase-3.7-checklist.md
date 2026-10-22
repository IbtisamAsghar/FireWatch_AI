# Phase 3.7 acceptance: Member 3 pipeline handoff

- [x] Join, time, spatial distance, missingness, units and empty-run acceptance
  verified through existing pipeline regressions plus new Parquet semantic checks.
- [x] Rainfall completeness, non-hour cutoffs, future exclusion and provider fill
  handling verified; missing values never become observed zero rainfall.
- [x] Actual child-process termination after committed features (exit 23), lease
  reclamation on attempt 2, successful completion and no duplicate alerts.
- [x] Cached real replay completed in dedicated child processes with Python
  DNS/socket access restricted to PostgreSQL and provider HTTP transports denied.
  Zero outbound attempts; a synthetic subprocess proves the blocking policy works.
- [x] Member 4 handoff: actual Parquet feature snapshots/memberships, schema,
  lineage and checksummed manifest. Member 1 handoff: typed run/status JSON.

## Real-data evidence

[Consolidated smoke](phase-3.7-smoke.json) completes all three real replay scenarios,
including the quiet empty run and degraded weather case, in a disposable migrated
PostgreSQL test schema. The interrupted active run recovers safely, and the resulting
handoff round-trips 12 features and 1,664 memberships. Historical cutoff and actual
processing timestamps remain distinct; live heads and region enablement are unchanged.

[Reviewed local handoff receipt](phase-3.7-handoff.json) records the persistent
`data/handoffs/module-3-v1/` package: 12 feature rows, 1,664 membership rows, three
completed run records and three job/status records. Its manifest digest is
`6395e9ff7177c1a4c1eec3bbc708f84bbe90c293838b0ccb841a66a0865ce5c7`.
Verification succeeds using the installed wheel with runtime dependencies only,
from a directory without `.env` and with application credential variables removed.

The artifact provides 17 feature values, 14 missing indicators and 13 metadata
columns. Empty-run exports retain a typed zero-row schema. Real zero and null remain
distinct. Member 4 must select only the declared 31 model inputs. Labels, targets,
event grouping, dataset splits and model evaluation remain Module 4 work.

## Regression and packaging

**706 tests passed**, including **20 new handoff/network tests** (13 PostgreSQL
integration cases and 7 unit cases). Full HTTP liveness/readiness/status/read/auth
smoke passed. One existing Starlette/httpx deprecation warning remains nonblocking.

CI-scoped Ruff lint/format (136 files), strict mypy (71 source files), scaffold/link/
template/Git-exclusion checks, dependency consistency and source/wheel builds passed.
The installed runtime-only wheel verified the real package without credentials or
an .env file. Configured-secret scanning passed across 238 nonignored repository
files. Final manifest/checksum/semantic verification and `git diff --check` passed.

New acceptance covers typed empty exports, null versus genuine zero, UTC readback,
relocation, CLI verification, duplicate/orphan/future membership rejection, feature
and missing-flag corruption, protected model-input whitelist, provenance completeness,
manifest binding, existing-output protection and staging cleanup on write failure.

Pinned PyArrow 21.0.0 adds actual Parquet support; no other dependency versions
changed. Both universal hashed locks agree: 44 runtime and 62 development packages.
No database migration is needed. The dedicated acceptance network guard is opt-in,
irreversible only inside its child interpreter, and is never installed in ordinary
API/worker processes. It is not an operating-system firewall/native-code sandbox.

## Acceptance traceability

- Joins and immutable revisions: `test_snapshot_persistence.py`,
  `test_feature_persistence.py`, `test_replay.py`, `test_pipeline_handoff.py`.
- Six/24-hour boundaries and future exclusion: `test_cluster_processing.py`,
  `test_feature_processing.py`, `test_weather.py` and Parquet membership tamper tests.
- Haversine 2 km boundary, antimeridian/high latitude and chaining behavior:
  `test_cluster_processing.py`.
- Exact units, genuine zero, unknown confidence/day-night, finite values and
  missing flags: `test_feature_processing.py` and `test_handoff_parquet.py`.
- Rainfall interval completeness, missing hours/variables and POWER fill sentinels:
  `test_weather.py`, `test_climate_statistics.py`, `test_feature_persistence.py`.
- Empty completed runs and typed empty exports: `test_worker.py`, `test_replay.py`,
  `test_pipeline_handoff.py` plus the real quiet scenario.
- Crashes, retry fencing, no duplicate outputs and offline execution:
  `test_worker.py`, `test_replay.py`, `test_handoff_parquet.py` and
  `scripts/verify_pipeline.py` real-data evidence.

See [handoff operating guide](pipeline-handoff.md) and
[committed schema](pipeline-handoff.schema.json). Local artifacts remain Git-ignored;
code, contracts, schema and sanitized receipts are versioned. No trained-model
accuracy, independent team review, blockchain submission or deployment is implied.
