# Historical training-data backfill

The original diagnostic dataset had twelve off-cadence snapshots and no eligible
training rows. The new cache-only backfill recomputes the frozen six-hour seasonal
design using the shared snapshot, enrichment, feature and target-label builders.

## Current real dataset

The completed `data/backfills/cadence-seasons-v1` job processed all 1,472 planned
cutoffs in 92 verified chunks. It generated 1,975 snapshots: 157 positive, 1,662
negative and 156 unknown labels. All 1,819 known labels are cadence-aligned and
eligible before whole-event split/purge checks. No source lookback cutoff was
missing. This fixes the original zero-eligible-row problem.

Eligibility is not model approval. Event-disjoint partitions, calibration,
comparison against baselines and independent release review remain separate gates.
The existing feature/target definitions and split boundaries have not been weakened.

Weather temperature is missing in 1,969 snapshots. Missing values remain null with
flags and are handled by training-only imputation. These rows establish a
satellite-feature cohort with sparse cached weather, not a fully weather-enriched
or operationally validated model. The weather fill planner produces 288 bounded
weekly requests. One real bounded request was successfully fetched and cached;
the frozen original job deliberately does not incorporate later retrievals.

## Commands

Run in the repository root with the locked Python 3.12 environment:

```powershell
firewatch backfill-training-data --output data/backfills/cadence-seasons-v1
# After interruption, keep the identical design/feature/outage configuration:
firewatch backfill-training-data --output data/backfills/cadence-seasons-v1 --resume
firewatch merge-backfill-labels --path data/backfills/cadence-seasons-v1 --output data/labels/cadence-seasons-v1
```

For a small pilot use `--start 2024-07-24T00:00:00Z --end 2024-07-27T00:00:00Z`
and a fresh output directory. Default chunks contain 16 cutoffs; supported chunk
sizes are 1–32. The root `job.json` binds the entire date plan, retrieval cutoff,
feature specification, design and outage catalog. Changing any of those on resume
is rejected.

Each chunk preserves current source revisions, satellite hashes, enrichment
values/pins and exact snapshot/feature hashes. Labels point to the SHA-256 of this
feature proof. Verify a chunk offline with its separately trusted manifest digest:

```powershell
firewatch verify-backfill-chunk --path data/backfills/cadence-seasons-v1/chunk-0000 --manifest-sha256 <trusted-chunk-manifest-sha256>
```

A complete merge produces the existing `target-label-package-v1` input expected by
`build-splits`, `train` and `evaluate`. It explicitly identifies backfill provenance
and has no invented pipeline handoff reference. Existing bounded dataset contracts
still apply: at most 10,000 snapshots and 256 MiB per merged label package; if
exceeded, the verified chunks remain available and the merge fails explicitly.

## Weather collection

```powershell
firewatch fill-backfill-weather --path data/backfills/cadence-seasons-v1 --plan-only
firewatch fill-backfill-weather --path data/backfills/cadence-seasons-v1 --offset 0 --max-requests 1
```

Use the returned `next_offset` for the next bounded batch. A provider/budget failure
returns `status=paused` and keeps the failed request's offset. Respect the existing
shared minute/hour/day budget and cooldown; rerun later when a budget is available.
Cached intervals are reused. Weekly batches contain at most four cells (28
weighted provider units), keeping within the existing per-request/minute bound.

Fetched weather is stored as immutable raw/normalized revisions. To incorporate
newly fetched weather, create a new job directory with a new retrieval cutoff,
then merge its labels and rebuild downstream development artifacts. Old features,
dataset manifests and experiment identities stay frozen.

## Isolation and integrity

Feature backfill uses repeatable-read, read-only PostgreSQL transactions. Its
transient Run objects are never inserted. It creates no jobs, published runs,
scores, alerts, live heads, model approvals or final-test ledger entries. Missing
current source days are recorded as gaps and produce no fabricated negative rows.
Incomplete follow-up keeps an unknown label through the existing target logic.

Weather filling is an explicit separate operation that writes only cached source
assets and normalized weather records; provider I/O occurs outside database
transactions. It does not alter already generated feature packages.

Completed chunks are verified before resume. New chunks and merged labels are
published by staging and atomic rename. Failed owned staging directories are
cleaned up; existing outputs are never overwritten. Keep all backfill/source data
private and Git-ignored. Manifest digests provide integrity, not source
authentication or evidence of model performance.

## Verified candidate and next evaluation

The new dataset passes the frozen split policy with grouped temporal CV. Its
separate calibration period has only four positives, so the existing sparse-data
fallback is retained. A real 32-tree, depth-8 Random Forest candidate is exported
at `data/models/cadence-seasons-v1`. The export passed deterministic retraining
and portable prediction parity. It is development-only and uncalibrated:
probability remains null. No model was registered, approved or activated.

The [public receipt](training-backfill-receipt.json) binds the exact backfill,
merged labels, splits and training package hashes. Reproduce the receipt with
`.venv/Scripts/python.exe -m scripts.verify_training_backfill`.

The next existing workflow step is development evaluation and cross-fitted
threshold selection:

```powershell
$receipt = Get-Content docs/training-backfill-receipt.json | ConvertFrom-Json
firewatch evaluate --training data/models/cadence-seasons-v1 --manifest-sha256 $receipt.training_manifest_sha256 --output data/evaluations/cadence-development-v1
```

Only after freezing the development protocol should the one-shot final evaluation
and independent limitations review be considered. No final performance claim or
approval follows from dataset eligibility or candidate fitting.

## Verification

All **1,091 PostgreSQL-backed regression tests passed**, including the new backfill
and weather-fill cases. The earlier focused backfill/original-label suite passed
24 cases. Ruff lint/format, strict mypy (99 source files), scaffold/local-link
checks, unchanged hashed dependency locks, source/wheel builds and isolated-runtime
pip consistency passed.

The installed wheel verified all four new CLI help commands and recomputed the
real 15-eligible-row pilot from an isolated directory without .env, with settings,
network, database connections and Random Forest fitting denied. Full-season
chunks, merged labels, event splits and the fitted forest passed their source-bound
export verification. One real bounded weather fetch was cached successfully.
One existing Starlette/httpx deprecation warning remains; hosted CI and independent
model approval are not claimed by these local checks.
