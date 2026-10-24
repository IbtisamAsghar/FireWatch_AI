# Phase 5.5 acceptance: CI and integration quality

- [x] Locked Linux/Windows dependency installation, Ruff, mypy and pytest retained.
- [x] PostgreSQL 16 service matches the planned deployment major; real migration
  and immutable-publication tests remain in the default regression suite.
- [x] Offline HTTP transcripts/factories, no standard CI provider keys, and an
  explicit external-network guard in standard pytest jobs.
- [x] Complete import → cluster → enrich → feature → baseline/synthetic-model →
  alert → typed HTTP → verification journey with source/model artifact auditing.
- [x] Separate manual opt-in live provider workflow with bounded requests,
  environment-only secret input and sanitized status report.
- [x] Deterministically fitted synthetic JSON model; no arbitrary downloaded pickle.
- [x] Canonical evidence/hash, frontend response DTO and frozen OpenAPI contracts.
- [x] Crash recovery, idempotency, outages, empty region, missing/corrupt model
  and replay/live isolation included in the retained integration suite.
- [x] Retained JUnit/coverage, OpenAPI/handoff, distributions and public build metadata.
- [x] Release-tag/PR dependency and secret scans with pinned tools, failure gating,
  narrow reviewed false-positive exceptions and no ignored vulnerability IDs.
- [x] Linux image build/non-root/CLI validation job and build target implemented.

Local verification results are recorded in [the receipt](phase-5.5-smoke.json).

The complete PostgreSQL 16 regression passed **1,239 tests**, with one existing
Windows symlink-privilege skip, in **1,767.58 seconds** with coverage enabled.
Collection began before the three live-smoke boundary tests and final synthetic
ML end-to-end variant were added. The final seven-test phase suite passed with
external network access blocked. The complete offline unit suite passed **894
tests**, with the same one skip; the offline worker/model/operator/replay failure
suite passed **75 tests**. These focused runs cover the final added behavior.

Ruff/format, strict mypy, actionlint 1.7.12, hashed-lock consistency, source/wheel
builds, installed runtime-only wheel/dependency checks, frontend handoff export,
and real installed-wheel HTTP boot/liveness/OpenAPI/failed-readiness checks passed.
Both pip-audit reports contain zero remaining advisories; both redacted Gitleaks
reports contain zero findings, with the generated negative control detected.
PyArrow is the only application/development package version changed in this phase.

Hosted Actions and Docker execution are distinct from implementing their checks:
this Windows workspace has no Docker executable/daemon, and no hosted run has
been triggered or claimed. A successful hosted `linux-container-build` remains
required release evidence. No image was published and no application deployment,
schema migration, model promotion or application `.env` change was made.

See [workflow details and reproduction](ci-and-integration.md). Phase 5.6 will
implement the full Compose stack and its operational startup acceptance.
