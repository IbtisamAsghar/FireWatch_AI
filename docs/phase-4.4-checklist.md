# Phase 4.4 acceptance: forest training and calibration

- [x] Deterministic scikit-learn Pipeline uses training-only imputation/scaling and
  the frozen 31-column feature/missing-indicator whitelist.
- [x] Bounded seed-controlled search covers tree count, depth, leaf size and both
  class-weight policies; ranking/calibration diagnostics drive development-only selection.
- [x] Chronological validation or purged grouped forward folds select settings;
  source-bound memberships recompute before fitting and final test remains sealed.
- [x] Chronological sigmoid calibration uses separate whole-event, purged fit/audit
  portions within the calibration block, with sample/class/event/variation gates.
- [x] Independent Brier/log-loss and reliability audit accepts or rejects calibration;
  insufficient/rejected/CV cases expose only `model_score`, with null probability.
- [x] Original chronological threshold block stays reserved; CV reports the need
  for cross-fitted threshold selection and does not claim an independent reserve.
- [x] Estimator trees, preprocessing, accepted/rejected sigmoid evidence, ordered
  names, parameters, versions, source identities, checksums and policy are saved.
- [x] JSON artifact validation/inference reproduces forest float32-input/double-
  threshold behavior and uses no executable pickle/joblib loading.
- [x] Offline CLI export/verification, relocation, source recomputation, tamper checks,
  dependency/path/node/size bounds and safe staging cleanup are implemented.
- [x] No API training, scorer promotion, final-test evaluation, new dependency or
  database migration; artifact-free baselines remain available.

See [policy and operator guide](forest-training-and-calibration.md),
[frozen training policy](../configs/forest-v1.yaml) and [real receipt](phase-4.4-smoke.json).

The real private package is `data/models/phase-4.4-final`, manifest SHA-256
`07596df56cbc16e2567ec35ff54f6ef5a82263c3a61d1c0d31d0a728d5f0189d`.
It correctly reports insufficient data and a null estimator: all twelve current
snapshots are off cadence. No real forest, calibrated probability, achieved metric,
model approval or promotion is asserted. Synthetic tests exercise adequate cohorts,
class-weight effects, acceptance/rejection, constant/missing inputs and leakage
guards. Prototype calibration gates do not establish operational validity.

## Local verification

944 tests passed, including 41 new Phase 4.4 cases (32 unit and nine
PostgreSQL/package integration cases). Full HTTP liveness/readiness/regions/status/
docs/OpenAPI smoke and operator authentication passed with the unprivileged
application role. The focused 32-case forest suite also passed after the final
CV threshold-status correction. One existing upstream Starlette/httpx TestClient
deprecation warning remains nonblocking.

CI-scoped Ruff lint/format, strict mypy (84 source files), scaffold/link/template/
Git-exclusion checks, unchanged hashed locks (44 runtime/62 development packages),
pip dependency checks, source/wheel builds and Git whitespace checks passed.
Configured-secret scanning passed over 302 nonignored repository files and 18
private forest-package files. Original pipeline, target-label, split and baseline
comparison manifests retain their reviewed hashes.

The final installed wheel verified the real no-fit artifact from an isolated
working directory without .env, credentials or network access. It also fitted and
round-tripped explicitly synthetic calibrated and forward-CV JSON models, including
the CV threshold-selection limitation. Those tests assert implementation behavior,
not real fire-model performance.
Hosted CI and independent human review are not asserted by local checks.
