# Phase 4.3 acceptance: baselines first

- [x] Baseline A preserves maximum-FRP-only scoring and existing evidence payloads.
- [x] Baseline B implements documented intensity, humidity, wind and previous-activity
  transformations, fixed weights, missing floors and explicit prototype limitations.
- [x] Sufficient development cohorts freeze reference scales on training features
  only; the current sparse real cohort uses prespecified, unvalidated prototype settings.
- [x] Rule outputs are `priority_score` and never expose an escalation probability.
- [x] Fixed logistic regression Pipeline fits only sufficient chronological training
  sets or explicit grouped forward folds; training-only preprocessing and exact
  fit/dataset binding prevent holdout access. Nonconvergence fails closed.
- [x] Every method uses identical held-out development row IDs and labels, with
  paired ranking metrics and no method-specific row dropping. Final test remains
  sealed for the Phase 4.5 release evaluation.
- [x] Both baselines run in the worker and API metadata without a model artifact;
  opting into weighted scoring does not change the default or prior run evidence.
- [x] Portable source-bound comparison export, relocation, tamper detection,
  dependency checks, offline recomputation and safe failure cleanup implemented.

See [policy and operator workflow](baselines-and-comparison.md),
[frozen catalog](../configs/baselines-v1.yaml) and [real receipt](phase-4.3-smoke.json).

The real comparison package is `data/baselines/phase-4.3-final`, manifest SHA-256
`2d50d21aa2d7ab4585a512f7a1e080f7e496ea65526dfc4eb5550da77e4ec180`.
All twelve snapshots are off cadence, so logistic fitting is skipped and no
held-out metric is claimed. The final test is sealed. Synthetic tests exercise
sufficient balanced chronological and CV cohorts; they are not real training data
or evidence of achieved model performance. Seasonal cadence-aligned feature/label
cohorts remain necessary before any real supervised fit or model promotion.

## Local validation

903 unique tests passed across the full 902-test regression and the final
focused run, including 36 new Phase 4.3 cases (27 unit and nine PostgreSQL/package
integration cases). Full HTTP liveness/readiness/regions/status/
docs/OpenAPI smoke and operator authentication passed with the unprivileged
application role. A final membership-lookup performance cleanup was followed by
52 focused baseline/split tests passing, including an additional synthetic
separable-signal test that verifies the logistic pipeline actually learns and
scores a disjoint validation cohort. One existing upstream Starlette/httpx
TestClient deprecation warning remains nonblocking.

CI-scoped Ruff lint/format, strict mypy (80 source files), scaffold/link/template/
Git-exclusion checks, unchanged hashed locks (44 runtime/62 development packages),
pip checks, source/wheel builds and Git whitespace checks passed. Configured-secret
scanning passed over 288 nonignored repository files and 16 private comparison
files. Original pipeline, target-label and split manifest hashes remain unchanged.
The final installed wheel verified the real comparison and scored both baselines
from an isolated working directory without .env, credentials or network access;
it also fitted and evaluated the explicitly synthetic logistic comparison path.
No database migration, new dependency, real supervised fit or scorer promotion.
Hosted CI and independent human review are not asserted by local checks.
