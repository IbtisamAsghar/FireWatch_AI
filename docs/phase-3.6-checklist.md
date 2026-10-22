# Phase 3.6 acceptance: deterministic replay

- [x] Three real historical scenarios: observed activity, archive-covered quiet
  interval, and incomplete cached-weather example. No synthetic provider outage
  is presented as real evidence.
- [x] Complete portable bundles with original raw bytes, bounded canonical input
  rows, exact satellite revision policy, cached weather/baselines, annual POWER
  dependencies, feature/clustering config and per-file SHA-256/size inventory.
- [x] `firewatch replay --scenario <id>` imports verified local inputs and executes
  a durable job using fixed historical as_of and an injected processing clock.
- [x] No live weather/source fetches, baseline downloads or latest-live publication.
  Verified historical bundles work while live region enablement remains disabled.
- [x] Every run/feature/score/evidence remains replay mode; historical cutoff,
  acquisition/valid time and actual later scoring/completion are separate.
- [x] Identical feature hashes and numeric results across re-admission, fresh
  databases, changed processing clock and relocated bundles. Tolerance: 1e-12;
  observed equality was exact within the locked environment.
- [x] Synthetic failure/security fixtures explicitly labeled and isolated from
  the real catalog and real-data acceptance reports.

## Measured evidence

[Real catalog](replay-catalog.json) declares `norcal-active-v1` (six snapshots),
`norcal-quiet-v1` (zero) and `norcal-degraded-v1` (six). The degraded case has
incomplete cached weather and null/flagged rolling rainfall in all six features.
It is not an asserted provider outage or an artificial modification of real data.

[Fresh-database smoke](phase-3.6-smoke.json) executes all three scenarios twice in
each of two disposable PostgreSQL schemas, including relocated bundle copies.
Provider HTTP is disabled; attempts are zero. Run IDs, feature hashes, baseline
scores, priorities, completeness and quality warnings match across fresh imports.
Replay never creates a live head or enables imported regions.

[Actual CLI smoke](phase-3.6-cli-smoke.json) records completed historical runs
persisted in the application database. Active/degraded runs each have two local
verified Critical alerts; the quiet run has none. Live pointers and region settings
are unchanged. Scores remain research baseline indices with null probabilities.

**686 tests passed**, including **19 new replay tests** (12 PostgreSQL integration
cases and 7 unit cases). Full HTTP bootstrap/auth acceptance passed. An existing
Starlette/httpx deprecation warning remains nonblocking. New tests cover import
portability, independent recomputation, actual CLI invocation, cache drift, tamper
before/after verification, missing files, path escape, duplicate/nonfinite JSON,
canonical identity conflicts, disabled-region policy and catalog mismatch.

CI-scoped Ruff lint/format (131 files), strict mypy (69 source files), scaffold
links/templates/exclusions, dependency locks (43 runtime/61 development packages),
pip dependency consistency and source/wheel builds passed. Runtime-only installed
wheel imports and replay CLI help passed. Configured-secret scanning passed across
228 nonignored repository files. `git diff --check` passed.
Actual ranked-list and summary APIs returned valid replay results for all three
persisted real runs. Downloaded bundles remain Git-ignored; active/degraded bundles
each contain 48 checksummed files (~7.76 MB), and the quiet bundle contains two
(~1.10 MB). No new credentials, dependency or database migration were required.

## Handoff

Use [replay operating guide](deterministic-replay.md) for commands, manifest trust,
copy/import rules, provenance limitations and immutable version handling. Raw
bundles live in ignored `data/replays/`; preserve/transfer those directories with
the matching catalog. Code, preparation specification, catalog hashes and sanitized
smoke receipts are versioned. No downloaded bytes or secrets are added to Git.

No dependency or migration changes are required. This phase does not claim model
validation, cloud-free coverage, blockchain submission or production deployment.
