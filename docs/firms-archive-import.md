# Historical FIRMS CSV import

Phase 2.4 adds a trusted local CSV importer with provenance, bounded commits,
resumable checkpoints and a real-data handoff. It calls the same `normalize_row`
function as the live/bounded normalizer; units, confidence handling, observation
keys and revision hashes are identical. No migration or dependency is added.

## Commands

Activate the project's Python 3.12 virtual environment and run from the repository
root. Downloaded CSVs and runtime output remain excluded from Git.

```powershell
firewatch import-firms --path <downloaded.csv> --manifest <provenance.json> --dry-run
firewatch import-firms --path <downloaded.csv> --manifest <provenance.json> --chunk-rows 500
```

`--dry-run` validates, preserves the original, builds the inventory and writes
quality evidence without loading environment settings or contacting PostgreSQL.
The normal command imports into an **existing** region with an exactly matching
bbox. Region setup uses the existing `firewatch seed-regions` command; import does
not activate a region. `--output` defaults to `data/archive-imports`.

`--invalid-frp reject|null` and `--missing-frp allow|reject` use the live policy.
The default is to reject invalid/negative FRP and permit missing FRP as flagged
null. Mixed satellite/product/explicit processing-family rows stop the entire
import during preflight. Other invalid rows are retained in rejection evidence;
their quality limitations remain visible in the handoff and default run selection
rejects batches containing rejected rows.

## Required provenance

The [JSON schema](firms-archive-manifest.schema.json) describes
`firms-archive-manifest-v1`. Actual, checksum-verified manifests for the current
real samples are [September 2022](manifests/historical-1-archive.json) and
[July 2024](manifests/historical-2-archive.json).

- Set `trusted_local_input: true` only for a known local CSV. No URL fetching,
  compressed-archive extraction or account operation is performed by this command.
- Record product and matching NOAA-20/21 sensor, region name and exact bbox.
- `interval.start_date/end_date` are **inclusive UTC calendar dates**.
- Record SHA-256 of the downloaded bytes, the actual UTC retrieval timestamp,
  source reference URL, attribution/license reference and optional archive request ID.
  Use the public FIRMS download landing page plus request ID for portal downloads;
  Public NASA `DL_FIRE_J1V-C2_<id>.zip`/`J2V` download references are also supported and checked against the declared sensor/request ID. Signed download URLs, credentials, query strings and private API paths are rejected.
- `source_kind` distinguishes `archive_portal` from `area_api_sample`. The current
  real samples remain clearly identified as API samples, not delivered seasonal orders.
- Declare `earliest_feature_cutoff` and `last_label_cutoff`. The archive must extend
  at least 24 hours before the first and 24 hours after the last. UTC half-open
  storage ends at midnight following the inclusive final calendar date. An
  insufficient interval fails before database writes. `cutoff_purpose` defaults
  to `proposed`; the current verification windows are not approved training splits.
- `covered_intervals` explicitly attests which requested date ranges were delivered
  completely. Without that declaration, missing/unknown coverage is reported and
  those daily batches cannot be selected. Sparse hotspot rows alone do not establish
  complete satellite or cloud-free coverage.
- Optional `expected_active_intervals` identifies periods requiring activity for
  review; a zero-observation day in such a period fails publication. Unconfirmed
  zero-observation spans of at least seven days, or an entirely empty archive,
  also require review. `confirmed_empty_intervals` can document reviewed no-fire
  periods without inventing detections. A confirmed empty archive imports zero rows.

For seasonal order 828115, the declared request interval is 2022-04-30 through
2022-11-01; order 828116 uses the same dates in 2024. Both files were subsequently delivered using the download links supplied by the user. Their actual checksums and retrieval times are in the [delivery evidence](source-access-archive-deliveries.json), with validated [2022](manifests/season-2022-archive.json) and [2024](manifests/season-2024-archive.json) manifests. Original ZIPs remain in `data/raw/archive-downloads`; exact decompressed CSV originals are preserved there and in their import directories.

```powershell
(Get-FileHash -LiteralPath <downloaded.csv> -Algorithm SHA256).Hash.ToLowerInvariant()
```

Weather and climate baseline history are a separate Phase 3 import requirement;
this importer does not synthesize weather or claim feature readiness.

## Streaming, transactions and resume

The original CSV is copied byte-for-byte into an import directory, with a streaming
checksum/size check before use. Originals are preserved unchanged. Preflight reads
UTF-8/BOM CSV one record at a time and writes a disk-backed SQLite index. It validates
row widths, required columns, row limits and normalizer semantics before writing
anything to the application database. Default bounds are 2 GiB and ten million
records; imports longer than ten years must be split. No seasonal list of raw rows
or normalized observations is held in memory.

The import ID hashes the full provenance manifest, policy and version; file paths
and chunk sizes do not change that identity. Keep the same output directory for
resume. Repeated imports verify the original again and read PostgreSQL checkpoints,
so a missing/stale local progress JSON is not the authority. Changing manifest
provenance/policy creates another source lineage, while database uniqueness still
prevents duplicate observation identities.

Each calendar day has **one** database ingestion batch. Transactions process at
most `--chunk-rows` unique revision snapshots (1–5,000, default 1,000), committing
canonical upserts, source revisions, counters and the database checkpoint together.
An interrupted chunk rolls back; earlier chunks remain saved and the day stays
`running`, unavailable to normal run selection. Rerun the same command to resume.
The day changes to `succeeded` only after its entire staged dataset reconciles.
Unverified or unexpectedly empty days become `failed`, with a fixed quality reason.
An advisory lock prevents two import processes from owning the same checkpoint.

Publishing complete days preserves the Phase 2.3 rule that a later full-day source
replaces the old day, including moved/deleted observations and valid empty results.
Publishing each row chunk as a separate full-day source would lose observations;
this importer deliberately avoids that error. See [revision selection](firms-normalization.md).

## Evidence and handoff

Each `data/archive-imports/<import-id>/` contains:

- `original.csv`: untouched source bytes.
- `manifest.json`: validated source provenance.
- `index.sqlite`: disk-backed normalized snapshots and complete source record-number
  provenance. Database revision row-number samples cap at 20; occurrence counts and
  this index preserve full duplicate provenance. More than 64 distinct correction
  variants for one observation fails preflight to bound database lineage size.
- `quality.json` and `rejected.jsonl`: bounded aggregate quality plus every rejected
  row and reason. No rejected rows are silently converted into negative labels.
- `progress.json`: convenience snapshot of the authoritative database checkpoint.
- `handoff.json`: source assets/batches, manifests, cutoff windows, inventory and
  explicit missing labels/weather/baseline requirements for Member 4.

The parent source asset retains the archive manifest and checkpoint. Daily assets
point to its UUID and original checksum. All variants of an identity remain in the
existing `firms-source-revisions-v1` source structure. PostgreSQL unique constraints
resolve repeated observations across files and regions; revisions remain lineage,
not extra activity.

Inventory buckets contain month, sensor, product, region, confidence class and
provider type code (0–3, otherwise unknown). The supervised `class_label` is
**unlabeled**; positive/negative counts are null. Empty months receive zero counts.
`distinct_fire_periods_proxy` groups adjacent observation times separated by more
than 24 hours. This is thermal activity, not a verified independent wildfire event
count; it cannot by itself justify event-separated evaluation.

Member 4 uses the exact saved batch IDs and Phase 2.3 `RevisionPolicy`. For a
seasonal dataset, filter `source_batches` to the required run window; a policy
accepts at most 128 source batches per run. Pin the chosen product per sensor and
exact source revisions before feature computation. Canonical first-seen database
measurements are not a replacement for selected revisions.

## Current real data

Both requested seasonal NOAA-20 SP CSV archives are delivered and imported:

- Request 828115, 2022-04-30 through 2022-11-01: **5,496** accepted, zero rejected.
- Request 828116, 2024-04-30 through 2024-11-01: **12,366** accepted, zero rejected.

The database contains **17,862 distinct observations** across 372 requested
calendar days. The earlier 6,404 API sample observations are shared identities;
they add lineage, not activity. Source associations total 24,266 across all four
inputs. Repeating both seasonal imports added zero observations. Monthly selection
checks returned every seasonal observation using the existing revision policy.
There are no undeclared date gaps or unexpectedly empty periods in the delivered
intervals. Empty hotspot days do not prove cloud-free or fire-free conditions.

The delivered portal files use `brightness` and `bright_t31` headers. All 6,404
overlapping API observations matched the corresponding `bright_ti4`/`bright_ti5`
values exactly. The shared parser/normalizer accepts both complete header pairs;
the original provider row and source column names remain in revision evidence.
NASA's [FIRMS format discussion](https://forum.earthdata.nasa.gov/viewtopic.php?t=6092)
also notes the shared column names in downloaded VIIRS files.

Both full seasons meet the 24-hour margins around their proposed May–October
cutoff windows. Cutoff approval/training splits, weather, climate baselines,
event grouping and supervised labels remain later work. The region remains
disabled pending independent source review.

The local Member 4 handoff is `data/member-4/real-data-manifest.json`; no external
message was sent. The [sanitized inventory](historical-data-inventory.json)
contains monthly sensor/region/confidence/provider-class totals and **43 distinct
temporal activity periods** under the documented 24-hour gap proxy. This number
does not establish 43 independent wildfire events or an event-separated split.
All 17,862 observations remain unlabeled and supervised training readiness is false.

See [acceptance](phase-2.4-checklist.md) and [real import verification](phase-2.4-smoke.json).
