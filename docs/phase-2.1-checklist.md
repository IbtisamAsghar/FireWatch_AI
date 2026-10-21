# Phase 2.1 acceptance: source access and first region

**Phase 2.1 source-access requirements complete: bounded source probes verified and both seasonal archive requests submitted. Both seasons were subsequently delivered/imported in Phase 2.4; independent team review remains pending.**

- [x] A valid local FIRMS MAP_KEY was used by the bounded probe and never written to repository evidence.
- [x] Full seasonal NOAA-20 CSV archive requests submitted through the authenticated FIRMS portal: `828115` (2022-04-30–2022-11-01) and `828116` (2024-04-30–2024-11-01). Both were initially observed with status `Submitted`; delivery and full seasonal import are now verified in Phase 2.4.
- [x] Authenticated availability for NOAA-20 and NOAA-21 NRT/SP was retrieved and recorded with exact date ranges.
- [x] Requested full-month intersections for each product were computed from provider dates and recorded in `configs/regions.yaml`.
- [x] Northern California study candidate selected from two official incident records; the 5-square-degree bbox contains both reported origins.
- [x] Bbox, May–October study season, requested 2022/2024 months, primary and comparison products, and sample windows are recorded.
- [x] One recent NOAA-20 NRT sample and two historical NOAA-20 SP samples downloaded and inspected. The recent response was valid header-only CSV (zero detections); the historical samples contained 1,367 and 5,037 rows.
- [x] Actual CSV columns, lexical types, categories, acquisition bounds and file checksums recorded in sanitized evidence.
- [x] Ten real historical-weather point probes at the center and four corners returned complete hourly values for both sample periods.
- [x] NOAA-20 is the primary cohort; NOAA-21 remains comparison-only and its products are not mixed into historical NOAA-20 samples.
- [x] Raw provider data stays ignored under `data/raw/`; committed evidence contains metadata/checksums only.
- [x] Missing-key, HTTP/CSV, date/coordinate/sensor/value, output-bound and log-redaction behavior is implemented in `sources/access.py` and covered by offline acceptance tests.

See [source access and region study](source-access.md) and the [sanitized probe evidence](source-access-firms.json). The primary region remains `enabled: false`, with source status `probed` (not `reviewed`). The [archive submission evidence](source-access-archive-requests.json) records both request IDs and exact parameters. Full seasonal processing/delivery and import are now complete in Phase 2.4; independent human review remains outstanding. Sample fixtures are invented software test data and are not evidence of source access.

Phase 2.1 adds optional `region-source-access-v1` metadata while preserving the existing `regions-v1` configuration contract. It does not change API DTOs, database migrations, or claim implementation of the Phase 2.2 production client, historical importer, normalization, worker or training pipeline.

Reanalysis on 21 October 2026: all **44 Phase 2.1 offline tests passed** after removing assumptions about the live configuration status. All **14 saved provider-response checksums** matched the sanitized probe evidence. Configuration parsing and month availability were consistent with the recorded source results.

## Phase 2.4 delivery follow-up

Both requested NASA archives were subsequently delivered and imported. Current
[delivery evidence](source-access-archive-deliveries.json) verifies the original
ZIP/CSV hashes, and the [seasonal inventory](historical-data-inventory.json) records
17,862 distinct observations. Earlier submission/pending statements above describe
the Phase 2.1 snapshot. Independent source review and region activation remain
separate; the candidate stays disabled.
