# Phase 2.6 acceptance and handoff

Acceptance scope: source ingestion and its handoff, including explicit dataset
limitations. Evidence recorded 2026-10-21; actual NASA data stays under ignored `data/`.

- [x] Same-file reimport creates no duplicate observations. PostgreSQL integration
  tests cover changed chunk sizes and crash recovery. Both real seasonal CSVs were
  reimported at chunk size 2,000: each inserted zero. All four database table counts
  stayed unchanged; see [sanitized evidence](phase-2.6-smoke.json).
- [x] Leading-zero times, empty responses, malformed CSV, timeout, rate limit and
  corrected records have executable regression coverage. Client tests exercise
  bounded retries and secret redaction; normalization tests preserve exact keys and
  correction lineage; importer/refresh integration tests verify durable publication.
- [x] Live/archive normalization uses the identical `DetectionInput` schema and
  UTC/MW/K/km conventions. Cross-path tests compare full observation payloads and
  revision hashes for all four products. Existing tests cover delivered archive
  brightness aliases; handoff acceptance checks versions, units and revision hashes.
- [x] Historical inventory is present and its evaluation limitation is flagged.
  Two seasons, 17,862 observations, 372 days and 43 activity-period proxies are
  available. Independent event IDs and labels remain unknown; event-separated
  evaluation and supervised-training readiness are explicitly false.
- [x] Configured secrets are absent from the scanned repository and supplied source
  artifacts. Regression tests cover logs, exceptions, provider echoes and chunked
  scanning, including a secret spanning a read boundary. A failed scan cannot
  publish a completed handoff manifest. Unknown credentials are outside this scan.

Implementation: [offline validation/export guide](firms-handoff.md),
`firewatch.sources.firms_handoff`, and `python -m scripts.verify_firms_handoff`.
The final private bundle is `data/member-4/phase-2.6-final-handoff/manifest.json`;
its checksums bind the normalized revision files, schema and ingestion-quality report.
No raw rows, private paths or credentials are included in the sanitized evidence.

Validation results are recorded in [phase-2.6-smoke.json](phase-2.6-smoke.json).
The new 20 handoff tests exercise corruption, secret detection, empty archives,
all-product parity, corrected revisions, refresh export and missing daily provenance.
Full PostgreSQL integration and HTTP smoke checks use the existing bootstrap verifier.

Phase 2.6 is complete with the documented dataset limitation. Region activation,
weather ingestion, scoring, model training and completed pipeline Runs are later work.
