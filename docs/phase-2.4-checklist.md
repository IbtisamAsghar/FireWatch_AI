# Phase 2.4 acceptance: historical import

**Phase 2.4 is implemented and verified with both delivered real seasonal archives.**

- [x] Two full seasons requested, delivered and preserved as untouched ZIP/CSV originals with SHA-256 verification.
- [x] `firewatch import-firms --path ... --manifest ...` supports trusted local CSV and offline preflight.
- [x] Provenance includes source URL, product/sensor, date interval, region/bbox, checksum, retrieval time and attribution/license reference.
- [x] Exact live normalizer reused, with streaming preflight, bounded database commits and resumable checkpoints.
- [x] Missing coverage, mixed products, duplicate revisions and unexpectedly empty periods are detected.
- [x] At least 24 hours before the earliest feature cutoff and after the last label cutoff are enforced and imported; weather/baseline history is explicitly separate.
- [x] Inventory covers month, sensor, region, confidence/provider classes and distinct temporal fire-activity periods; supervised classes remain unlabeled.
- [x] Local Member 4 handoff contains real source manifests, batch IDs and limitations without invented labels.

## Real-data verification

Both NASA ZIPs were downloaded from user-supplied delivery links on 21 October
2026 and verified before extracting bounded, safe CSV members. Original archives
and exact CSV bytes remain in ignored local storage.

- 828115 / 2022-04-30–2022-11-01: **5,496 accepted, 0 rejected**.
- 828116 / 2024-04-30–2024-11-01: **12,366 accepted, 0 rejected**.
- **17,862 distinct observations**, including the 6,404 prior sample identities;
  no duplicated activity from overlapping files. All 372 declared calendar days
  are represented as completed daily batches. No declared coverage gaps or
  unexpectedly empty periods remain in either source interval.
- Bounded first imports used 500 revision snapshots per transaction. Repeats used
  1,000 and inserted **zero** new observations. Global totals including samples:
  24,266 source associations, 378 daily batches and 382 source assets.
- All 17,862 observations were retrieved through 16 monthly revision-selection
  checks. ZIP and preserved CSV hashes match the saved delivery manifests.
- The delivered legacy brightness header pair matched all 6,404 overlapping API
  rows; both schemas use the same normalizer, keys, units and quality rules.
- The configured region remains disabled; no labels, forecasts, weather history
  or independently reviewed event IDs were invented.

## Automated validation

**369 tests passed**, including real PostgreSQL migrations, upserts, interrupted
resume, checkpoint atomicity, concurrent ownership, complete-day publication,
single-connection pool operation, corrected geometry, valid-empty declarations,
mixed products, malformed CSV, checksum/size/row limits, actual header aliases,
manifest safety, 24-hour margins and offline CLI behavior. Normal API/health/auth
HTTP smoke passed. Configured Ruff/format checks passed, strict mypy passed for
57 source files, scaffold/link checks and dependency-lock consistency passed,
and wheel/source distributions built successfully.

The [inventory](historical-data-inventory.json) reports **43 temporal activity
periods** using a documented >24-hour observation-gap rule. These are proxies,
not independently verified wildfire events. Supervised labels, event grouping,
class balance and event-separated evaluation readiness belong to later phases.
Weather/baseline history remains Phase 3 work. These dependencies do not block
the completed archive importer. Hosted CI success is not claimed.

See the [operator guide](firms-archive-import.md), [manifest schema](firms-archive-manifest.schema.json),
[delivery evidence](source-access-archive-deliveries.json) and [database verification](phase-2.4-smoke.json).
