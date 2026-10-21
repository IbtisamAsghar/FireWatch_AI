# Phase 2.3 acceptance: normalize and deduplicate

- [x] Parse date plus zero-padded acquisition time in UTC, including `0037`.
- [x] Validate coordinates and requested bounds; apply configurable negative/invalid/missing FRP policies.
- [x] Preserve raw confidence and map supported classes without invented percentages.
- [x] Retain product, sensor, processing state, brightness, scan/track and day/night with explicit missing/invalid flags.
- [x] Version deterministic keys using product/sensor/time and exact source coordinate and scan/track decimal values.
- [x] Keep NOAA-20 and NOAA-21 identities distinct even at identical coordinates.
- [x] Preserve corrected snapshots, select one product per sensor and freeze run inputs without counting revisions as additional activity.
- [x] Persist a bounded quality summary plus full rejected-row evidence beside the original source.
- [x] Use PostgreSQL uniqueness/upserts across repeats, independent fetches, overlapping regions and concurrent transactions.

Implementation details and operator usage are in the [normalization guide](firms-normalization.md).
The existing schema's source JSONB and immutable run manifests carry versioned
revision history and pins; no migration or shared DTO change is required.

## Verification

Local PostgreSQL 16 verification on 21 October 2026 covers parser edge cases,
raw confidence, exact coordinate identity, NOAA separation, FRP policies, duplicate
variants, manifest tampering and offline CLI behavior. Database acceptance covers
idempotency, revision lineage, product selection, latest full-day replacement,
valid-empty replacement, retrieval cutoffs, partial-quality opt-in, region
associations, concurrent upserts, rollback and reproducible run pins.

The [real smoke evidence](phase-2.3-smoke.json) used the already downloaded
NOAA-20 SP response for 7 September 2022: **323 accepted, 0 rejected**. In a
disposable migrated PostgreSQL schema, the first write inserted 323 observations;
the repeat inserted 0. Counts remained 323 detections/323 source associations,
one asset and one ingestion batch. All 323 selected run inputs were pinned and
returned unchanged on repeat selection. The temporary schema was removed. No new
NASA request or application-schema data modification was needed.

Final local validation: **339 tests passed**, real API/health/authentication smoke
passed, configured Ruff checks and formatting passed, strict mypy passed for 54
source files, scaffold/link checks passed, and wheel/source distributions built
successfully. Hosted CI is not claimed. Seasonal archive delivery is not a prerequisite for this completed
normalization phase; consuming those archives remains Phase 2.4.
