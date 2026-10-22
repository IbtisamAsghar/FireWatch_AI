# Phase 3.5 acceptance: durable jobs and orchestration

- [x] Standalone `python -m firewatch.worker`; no scheduler inside Uvicorn.
- [x] Concurrent schedulers admit one refresh per enabled region/UTC slot.
- [x] Transactional due-job claims, expiring leases, background heartbeats,
  monotonic attempt fencing, bounded exponential retries and terminal failure.
- [x] Full ingest -> snapshot -> cached/live weather and precomputed climate ->
  features -> baseline score -> immutable evidence/optional outbox -> publication.
- [x] Independently committed idempotent checkpoints; enrichment/features commit
  atomically through the shared Phase 3.4 repository.
- [x] No row/advisory locks or database transactions around provider requests,
  normalization or scoring; acceptance acquires the job lock during those calls.
- [x] Normalized region/mode/cutoff claim identity plus deterministic unique run key.
- [x] Crash recovery after every stage and after publication; no duplicate scores,
  alerts or optional outbox records. Completed final attempts are acknowledged.
- [x] Latest-live publication only after all required outputs; failures preserve
  prior results and cached replay never advances the live pointer.
- [x] Partial coverage/quality warnings separate from stage success. Configurable
  3h weather/24h detection stale policies; no silent missing-to-zero enrichment.
- [x] Operator refresh CLI, API job/status integration, settings and operating guide.

## Verification

See [real-data smoke](phase-3.5-smoke.json): six feature sets, six FRP baseline
scores and two verified immutable alerts. A separate Python process exits without
cleanup after the features checkpoint (exit 23); the next worker reclaims its lease
and completes on attempt 2. Repeating admission retains identical feature hashes
and row counts. Provider calls: zero. Source data is real cached July 2024 data,
processed retrospectively in a disposable PostgreSQL test schema. Application
region enablement and latest-live records are untouched.

Synthetic PostgreSQL tests separately cover real provider-shaped live execution,
source failures and chunk reuse, weather outage/null flags, concurrent scheduling,
claim exclusion, heartbeat/expiry, stale writers, bounded retries, empty results,
optional outbox deduplication and publication boundaries. Synthetic data is never
presented as real acceptance evidence.

**667 tests passed**, including **32 new worker/scoring tests** (24 PostgreSQL
integration cases and 8 unit cases). The full `verify_bootstrap.py` run also passed
real HTTP liveness/readiness/read/status/docs/auth checks with the unprivileged
application role. One existing Starlette/httpx deprecation warning remains nonblocking.

CI-scoped Ruff lint/format (125 files), strict mypy (67 source files), scaffold/link
checks, exact locks (43 runtime/61 development packages), pip dependency checks,
source/wheel builds and runtime-only installed-wheel worker/refresh help checks
passed. Configured-secret scanning passed across 216 nonignored files.
`git diff --check` passed. No schema migration or dependency changes were needed.
The new real-data verification script is included in CI lint/format scope; its
real cached inputs are intentionally not a hosted-CI dependency.

## Scope boundaries

The required vertical slice includes `baseline-v1` FRP-only scoring and local
canonical evidence; thresholds are prototype research policy with no predictive
validation or emergency meaning. Probability is always null. Module 4 retains
labels, comparison baselines and evaluated ML. Phase 3.6 retains scenario bundles
and scenario CLI orchestration. Module 5 retains chain submission/confirmation and
production service deployment. No automatic worker is installed or left running.

See [operating guide](durable-worker.md) for exact commands, lease policy and recovery.
