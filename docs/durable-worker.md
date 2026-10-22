# Durable worker and orchestration (Phase 3.5)

Run `python -m firewatch.worker` in a dedicated process beside the API. Scheduling
is never installed in Uvicorn. Native Windows uses the existing Python environment
and PostgreSQL 16; no new service or package is required.

```powershell
.\.venv\Scripts\python.exe -m firewatch.cli refresh --region <enabled-region> --idempotency-key refresh-demo-0001
.\.venv\Scripts\python.exe -m firewatch.worker --once --no-schedule
# Continuous scheduler/consumer (stop with Ctrl+C):
.\.venv\Scripts\python.exe -m firewatch.worker
```

`refresh` only admits a job and prints its UUID. Query the authenticated existing
`GET /api/v1/jobs/{id}` endpoint for attempts, leases, checkpoints and result run.
Repeating a CLI idempotency key reuses its original region/window rather than
moving the cutoff. API submissions use the same queue and admission protocol.
`--once` processes at most one job; its exit code describes the worker invocation,
not the result of that job. Inspect job status for success/failure.

## Scheduling and leases

Enabled database regions are the configured/seeding source of truth. The scheduler
floors UTC epoch time to `REFRESH_INTERVAL_MINUTES` (default 15), then uses a unique
`schedule:{region UUID}:{UTC slot}` key. Multiple scheduler processes cannot create
two jobs for a slot. Disabled/oversized regions are skipped. Missed historical slots
are not backfilled; restart schedules the current slot. Queue capacity is shared
with operator submissions. Regions must be explicitly enabled before live use;
this phase does not enable the candidate region or install an unattended service.

Claims use PostgreSQL row locks with `SKIP LOCKED` and a short shared admission
lock. Due queued refresh/replay jobs and expired running refresh/replay jobs are
eligible. Anchor jobs belong to the separate optional chain executor; this pipeline
worker leaves their status, attempts and leases untouched. An anchor-only queue is
idle for this worker, rather than consuming and permanently failing those jobs. Active work for the
same region/mode/normalized UTC cutoff prevents an overlapping claim. Run UUID/key
also derive from those three values. A second request with changed versions,
window, scenario or assets conflicts instead of replacing the run's frozen inputs.

`attempts` increases on every claim and acts as the ownership fencing token. Every
stage write locks and checks the job's status, attempt and unexpired lease in the
same transaction as its outputs. An old worker cannot commit after reclamation.
A separate thread renews the lease every third of its duration. Provider requests,
normalization and scorer inference execute outside database transactions/locks.
Only bounded persistence and computation stages hold short transaction locks.

Defaults: `WORKER_LEASE_SECONDS=120`, `WORKER_MAX_ATTEMPTS=5`,
`WORKER_RETRY_SECONDS=30`, `WORKER_POLL_SECONDS=2`. Transient failures requeue with
exponential delay; a satellite Retry-After is respected, capped at one day. Invalid
configuration/input fails permanently. Exhausted expired attempts become failed;
a run already published before its last worker crashed is acknowledged succeeded.
Errors saved on jobs/logs are fixed codes, never provider bodies, tokens or URLs.
Ctrl+C/SIGTERM stops admission after current work finishes; abrupt termination
relies on lease expiry and checkpoints. No lock survives a provider network call.

## Frozen stages and publication

1. **Ingest:** live fetches both NRT products over overlapping 48-hour calendar
   coverage, saving each normalized chunk and checkpoint atomically. Retries skip
   committed chunks. Cached replay pins explicit imported asset UUIDs and never
   constructs a live client. Complete source-day coverage is required; an entirely
   rejected nonempty source fails instead of becoming an apparently empty region.
2. **Snapshots:** reuse the Phase 3.3 source pins and deterministic DBSCAN builder.
3. **Weather/baseline and features:** live fills bounded missing weather cells;
   historical jobs only read cache. Climate references are precomputed with
   `build-baselines`, never downloaded in a run. A weather outage leaves null
   inputs/quality flags and can produce partial coverage. Phase 3.4 atomically
   freezes enrichment and writes features, so these two checkpoints commit together.
4. **Score:** execute the frozen baseline outside a transaction, then save results
   and checkpoint together. Unsupported models fail; there is no silent fallback.
5. **Evidence/outbox:** every Critical score gets one immutable canonical payload
   and SHA-256. Alert IDs derive from score IDs; optional outbox IDs derive from
   alert/network. Evidence, optional outbox and checkpoint commit atomically.
6. **Publish:** existing completion guards validate all stages and outputs. Run
   completion and latest-live pointer commit together. Older cutoffs cannot replace
   a newer live head; replay never updates it. Partial-coverage warnings are stored
   separately from succeeded stage status. Failed work leaves the old head available.

Run manifests pin the request, bbox, feature configuration, source assets/revisions,
retrieval cutoff, scorer, evidence version and optional network. Retries reuse these
values. No dependency or database migration is required for this phase.

`WEATHER_MAX_AGE_HOURS` defaults to 3; `DETECTION_MAX_AGE_HOURS` defaults to 24.
API freshness evaluates current time for live data and historical cutoff for replay.
Valid empty fetches have fresh retrieval status; observed detections, when present,
can independently make source status stale. Informational weather conventions are
kept as quality metadata; missing/partial/stale inputs remain explicit warnings.

## Baseline and evidence support

The first runnable scorer is `baseline-v1`, an **FRP maximum only** ranking in MW.
Low is below 10 MW, Medium is `[10,50)`, High is `[50,100)`, and Critical is at least
100 MW. These are frozen prototype research thresholds, with no demonstrated
predictive skill or physical severity meaning. Missing FRP receives a ranking floor
of 0 with an explicit missing reason/flag; its feature stays null. Explanations name
the observed FRP and exact feature hash. `score_kind=baseline`, `model_id=null`, and
`escalation_probability=null` always. Weighted baselines, labels, model training,
calibration and evaluated priority policy remain Module 4 work.

`evidence-v1` encodes sorted compact JSON as UTF-8, ASCII escapes enabled, nonfinite
numbers forbidden. Payloads bind run/mode/cutoff, score/explanation, complete feature
DTO and pinned satellite/enrichment manifests. SHA-256 covers the exact saved bytes.
The existing database prevents changes to saved alerts and completed outputs. This
is tamper detection, not an external authenticity signature. Optional blockchain
submission/confirmation and anchor-job execution remain Module 5 work. The worker
never sends a chain transaction and never silently marks an anchor job succeeded.

## Acceptance and recovery

Synthetic tests use disposable migrated PostgreSQL schemas, clearly separate from
real acceptance. They cover concurrent scheduling/claims, normalized UTC identity,
heartbeat extension, stale ownership, capped retries, source chunk reuse, weather
outage, empty results, no row locks during provider/inference calls, optional outbox
uniqueness, and crashes after every checkpoint and after publication before job ack.

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_bootstrap.py
.\.venv\Scripts\python.exe -B scripts/verify_worker.py --source-run-id <cached-snapshot-run-UUID>
```

The second command copies bounded cached records into a disposable `firewatch_test`
schema. A separate Python process exits without cleanup immediately after features;
the next worker reclaims the expired lease and completes scoring/evidence. A repeat
submission verifies unchanged feature hashes/counts. It never enables an application
region, changes an application live head, or calls providers. Its source bundle must
already be available locally; it is not the Phase 3.6 replay scenario pack.
See [real smoke evidence](phase-3.5-smoke.json) and [acceptance](phase-3.5-checklist.md).
