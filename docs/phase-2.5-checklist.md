# Phase 2.5 acceptance: idempotent and observable refresh

- [x] Every new refresh ingests an overlapping 48-hour window; provider calendar
  requests cover it and normalized handoff records use the exact acquisition cutoff.
- [x] Requests stay inside the 1–5-day provider limit and each chunk has a durable
  checkpoint, bounded retries and quota-aware deferral.
- [x] NOAA-20/21 NRT products retain independent batch/attempt states; concurrent
  region owners and conflicting idempotency keys are refused.
- [x] Per-product fetched/accepted/rejected/duplicate counts, latest acquisition
  and last successful retrieval are reported through typed summaries and status.
- [x] Failed sensors/chunks retain partial/unavailable coverage with unknown counts;
  valid empty responses are explicitly distinct.
- [x] Member 1 receives additive fields in the existing status API, and Member 3
  receives typed, checksummed normalized records with batch/revision provenance.

The [refresh guide](firms-refresh.md) documents commands, persistence, API fields
and the worker boundary. No migration, package dependency, scheduled automation,
region activation or pipeline completion is required/claimed.

## Local acceptance

The final full suite passed **392 tests**, including independent-product status,
per-sensor freshness and failed raw-recovery regressions. Normal API/health/auth
HTTP smoke passed, configured Ruff/format checks passed, strict mypy passed for
60 source files, scaffold/link and lock consistency checks passed, and source/wheel
distributions built successfully.
Coverage includes exact UTC/calendar windows, product validation, duplicate
upserts across overlapping runs, corrected revisions, cached retry without HTTP,
Retry-After deferral, earlier failed chunks, raw-response crash recovery,
tampered raw evidence, disabled regions, key conflicts, per-region lock ownership,
single-connection operation, row quality and existing API/security contracts.

Real smoke evidence on 21 October 2026:

- NOAA-20 NRT: **45 fetched, 45 inserted, 0 rejected**.
- NOAA-21 NRT: **40 fetched, 40 inserted, 0 rejected**.
- **85 normalized records** verified inside the exact 48-hour window with matching
  handoff SHA-256; both requested product states were complete.
- Cached resume with no provider client changed no rows and made no HTTP request.
- Normal status API returned HTTP 200 and the correct per-source counts.
- Study region remained disabled and no pipeline Run was created.

See the [sanitized evidence](phase-2.5-smoke.json), [request schema](firms-refresh.schema.json)
and [summary schema](firms-refresh-summary.schema.json). Historical import evidence
remains scoped to its earlier phase; source refresh adds 85 current NRT observations.

The generic job worker, recurring refresh scheduling, weather/feature computation,
score publication and training labels remain later Module 3/4 work. Hosted CI
success is not claimed.
