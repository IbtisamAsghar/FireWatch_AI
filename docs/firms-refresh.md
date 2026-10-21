# FIRMS refresh: idempotency, progress and source status

Phase 2.5 implements the callable FIRMS ingestion stage and an operator CLI.
It fetches an overlapping **48-hour acquisition window** on every new refresh,
uses the existing normalizer/upsert repositories, and publishes source status
and a versioned normalized-record handoff. It does not complete a pipeline run,
compute scores or replace the future Module 3 job worker.

## Operator commands

From the project root with the Python 3.12 environment active:

~~~powershell
firewatch refresh-firms --region northern-california
firewatch refresh-firms --resume <refresh-id>
~~~

Both NOAA-20 NRT and NOAA-21 NRT are requested by default. Repeat --product to
select an explicit subset; SP products and duplicate products are refused.
--as-of accepts an aware ISO-8601 timestamp. The default is current UTC.
New cutoffs cannot be in the future or older than MAX_REFRESH_AGE_HOURS
(default 72). The exact logical start is always as_of minus 48 hours.

NASA requests use calendar days, so a rolling window can require three UTC
dates. The default --chunk-days 5 combines that range into **one bounded request
per product**; each request remains within NASA's 1–5-day limit. Counts refer
to the full requested calendar dates. The Member 3 record file filters acquisition
timestamps to the exact inclusive 48-hour interval.

--chunk-days 1 provides smaller checkpoints, but six separate requests consume
more of the existing shared quota budget. The client retains bounded retries,
deadlines, quota reservations and Retry-After handling from Phase 2.2. A deferred
chunk remains failed/unknown with next_retry_at; the service never sleeps through
a long quota delay or bypasses the ledger. Resume after the reported retry time.
Only chunks needing work are fetched; committed successes are reused.

--request-key pins an idempotent request identity within the region. The same
key with changed cutoff/products/policy is rejected. To retry the default
current-time command, use its saved --resume ID: invoking a new command without
that ID intentionally starts a new overlapping refresh. Cached results retain
their original per-attempt counts; those counts do not mean new rows were
inserted by the retrying CLI process. A fresh overlapping response reports
existing observation identities as duplicates.

The study region remains disabled pending independent source review. The
explicit --allow-disabled option supports bounded manual source validation
without activating the region. Normal operator enqueue policy still rejects
disabled regions. No scheduler, background service or recurring automation
is started by this command.

Exit codes: 0 for complete source coverage, 2 for partial/unavailable source
coverage (a valid summary is still printed), and 1 for configuration, storage
or database failure. A missing MAP_KEY is not fabricated into an empty response.

## Durability and correction semantics

A refresh stores its immutable specification, pinned normalizer/key versions
and JSON checkpoint in a parent source asset. This reuses existing database
tables; no migration or dependency is added. Each product/calendar chunk has
its own pending/running/succeeded/failed state, attempt count and batch identity.

Before HTTP, a running ingestion attempt is committed with unknown counts.
The FIRMS client receives the reserved raw-fetch UUID, allowing recovery of its
saved, checksummed manifest after a crash. A successful normalization atomically
replaces the empty reservation with the canonical source batch, upserts
observations/source revisions and advances the parent checkpoint in one
transaction. Failed attempts remain visible, with fixed error codes and
counts_known=false. Their database counter defaults are never exposed as
known zero observations.

A PostgreSQL advisory lock allows only one source refresh owner per region.
Each short transaction shares that connection, including with a single-connection
pool; HTTP occurs outside write transactions. No in-memory-only checkpoint or
cross-process mutex is required.

If a process exits after saving raw CSV but before committing normalization,
resume verifies and imports the saved response without fetching it again.
Incomplete/missing/corrupt raw evidence is recorded as interrupted, then
retried under a new attempt identity. Committed chunks are never re-fetched.
Progress JSON is a convenience copy; PostgreSQL is authoritative.

The Phase 2.3 product/day selection policy applies to every completed response.
Later full-day responses replace old source membership, including moved/removed
records and valid empty results. Canonical first-seen measurements are identity
records: processing uses selected revisions. Frozen completed-run inputs are
not changed by a later source refresh.

## Coverage and counts

- complete: every requested chunk for that product succeeded with no rejected rows.
- partial: usable observations, or valid empty responses, are available for some
  source coverage, while a chunk failed/pends or row validation rejected input.
- unavailable: the refresh has no usable successful source coverage.

A failed sensor's total fetched/accepted/rejected/duplicate counts are null,
not zero. known_successful_counts retains explicit subtotals from successful
chunks; zero successful chunks do not prove zero fires. A valid header-only
CSV is a successful known-zero observation result. Rejected rows remain in the
existing private quality evidence.

accepted means newly inserted global observation identities. duplicate includes
already saved observations and additional revisions; fetched reconciles with
accepted + rejected + duplicate. NOAA satellites and selected products remain
separate. Latest acquisition is the maximum known accepted timestamp in the
logical window; last successful retrieval is tracked independently. Missing or
stale data is not a negative fire label.

## Member 1: existing status API

GET /api/v1/status retains its existing API-v1 endpoint and read/auth policy.
Each source gains additive fields:

- refresh_id and coverage;
- fetched_count, accepted_count, rejected_count and duplicate_count;
- completed_chunks, total_chunks and next_retry_at.

The existing last_attempt_at, last_successful_retrieval_at,
latest_detection_at and latest_attempt_succeeded remain available. Status uses
the latest refresh that actually requested each product, not merely the newest
region-wide refresh. A failed earlier chunk cannot be hidden by a later
successful chunk. Retrying one sensor does not make the other's old retrieval
fresh: stale status uses that sensor's successful retrieval time.

The latest_detection_at field remains the most recent historically known
accepted source acquisition; it is not a current fire count. Legacy batches
without a complete refresh manifest retain their prior status behavior and
known successful counters. Unrequested/never-fetched products remain explicit.

The [public field dictionary](api-field-dictionary.json) is regenerated with
these fields. Private paths, request manifests, provider errors and credentials
are not returned by the status API.

## Member 3: callable stage and normalized handoff

Call firewatch.pipeline.firms_refresh.refresh_firms(engine, client, spec)
or resume using the saved UUID. RefreshSpec is described by the
[request schema](firms-refresh.schema.json); the result uses the
[summary schema](firms-refresh-summary.schema.json).

Each private data/firms-refresh/<refresh-id>/ directory contains:

- request.json: immutable UTC cutoff/product/policy specification;
- attempt-<fetch-id>.json: sanitized attempt request evidence;
- progress.json: a copy of durable per-chunk state;
- normalized-records.jsonl: SelectedDetection records using the exact normalizer
  DTO, source batch, revision hash and observation identity;
- handoff.json: summary, source batches, record count/path/SHA-256 and explicit
  partial_coverage_is_not_zero_fires=true / pipeline_run_completed=false flags.

Worker integration should pass the job cutoff as spec.as_of, retain the coverage
flag, verify handoff SHA-256 and pin source revisions before downstream processing.
Partial coverage must remain partial in later runs and labels. This source stage
does not create a Run or advance a live run head; Module 3 orchestration,
weather, features and scoring remain their later phases. No external message
was sent to team members.

## Real verification

On 21 October 2026, the configured NOAA-20/21 source refresh fetched **45** and
**40** rows, respectively, with zero rejected rows. All **85** selected records
were inside the exact 48-hour handoff window. Both product coverage states were
complete. A cached retry with no provider client changed no database row counts,
and the normal status API returned HTTP 200 with correct source counts.

The region stayed disabled and no pipeline Run was created. The previous
17,862 historical observations remain saved; total observations after this
live source smoke are 17,947. Downloaded rows and credentials remain excluded
from source control. See [aggregate evidence](phase-2.5-smoke.json) and the
[acceptance checklist](phase-2.5-checklist.md).
