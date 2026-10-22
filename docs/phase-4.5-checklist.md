# Phase 4.5 acceptance

Implemented the complete offline evaluation and priority-threshold workflow.
Empirical release evaluation remains data-gated, with no achieved performance
claim. See [protocol](evaluation-and-thresholds.md),
[policy](../configs/evaluation-v1.yaml) and [real receipt](phase-4.5-smoke.json).

- [x] Precision/recall/F1, prevalence, confusion matrix, separately named AP and PR-AUC.
- [x] Per-run P@10/R@10, small-run k and zero-positive recall exclusions/counts.
- [x] Brier and deterministic standalone reliability SVG for supported probabilities only.
- [x] Event/month/sensor-product/weather/unobserved-follow-up breakdowns.
- [x] Whole-event paired bootstrap with insufficient-event uncertainty status.
- [x] Both baselines, logistic, forest and refitted weather/climate ablations.
- [x] Provisional score-scale thresholds selected on development rows only.
- [x] Nested forward selection prevents CV threshold-fold hyperparameter leakage.
- [x] Durable transactional final holdout claim before scoring; concurrency tested.
- [x] Consumed failed claims, exact-result crash recovery and no second experiment.
- [x] Source-bound atomic packages, rehashed tamper rejection and offline CLI verification.
- [x] Model card documents target, failures, sampling/coverage and generalization limits.
- [x] Conservative paired-heldout recommendation; no automatic model promotion.

The private real package is `data/evaluations/phase-4.5-final` (a development-stage
package despite the local handoff directory's name), manifest SHA-256
`4e002768fc7a1b73e0331587e6c44ae3eb70890840e6883a60072473166aa12a`.
Its twelve diagnostic snapshots are all off cadence: zero eligible evaluation
rows, null evaluation models, no selected ML thresholds, no claimed metrics,
no reliability figure, no ablation finding. Final test remains sealed and no
final-test ledger was consumed. Prespecified baseline display thresholds remain
explicitly provisional. Existing source/training package hashes are preserved.

## Verification

The focused evaluation suite passes 18 synthetic unit cases, including six-method
release evaluation, nested-CV selection, calibration charts, input validation,
ledger concurrency, consumed scoring failures and post-publication recovery.
Nine PostgreSQL/package cases exercise relocation, provider/DB denial, CLI,
rehashed tampering, insufficient-final refusal and atomic staging cleanup.
The full PostgreSQL-backed regression passed **971 tests**, including these 27 new
Phase 4.5 cases. HTTP liveness/readiness/regions/status/docs/OpenAPI and operator
authentication passed using the unprivileged application role. One existing
upstream Starlette/httpx TestClient deprecation warning remains nonblocking.

Ruff lint/format, strict mypy (92 source files), scaffold/link/template/exclusion
checks, hashed dependency consistency (54 runtime/71 development packages),
`pip check`, source/wheel builds and Git whitespace checks passed. The installed
wheel verified the real sealed package from an isolated working directory with
credentials removed and provider/database calls denied. It also round-tripped
synthetic calibrated and nested-CV evaluation models and verified all three CLI
help commands. Reliability-chart layout was visually checked on synthetic data.

Configured-secret scanning passed over 319 nonignored repository files and 23
private evaluation-package files. Original pipeline/label/split/baseline/forest
manifest hashes remain unchanged. The authoritative final-test ledger does not
exist because the actual final test was never consumed. Hosted CI and independent
human review are not asserted by these local checks.
Synthetic metrics are test assertions, not fire-model performance evidence.

## Data needed for empirical completion

Historical on-cadence feature snapshots with provenance-bound follow-up,
reviewed event partitions, both classes and adequate independent groups spanning
the frozen development and final periods are required. Existing seasonal FIRMS
observations alone cannot replace those feature/label examples. No user action
is needed to use or review the implementation; data is needed before releasing ML.
