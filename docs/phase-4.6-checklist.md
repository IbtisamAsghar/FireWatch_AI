# Phase 4.6 acceptance

Implemented the batch inference and deterministic explanation contract.
See [operator and mathematical protocol](inference-and-explanations.md),
[feature contract](inference-feature-contract-v1.json),
[score schema](inference-score-v1.schema.json) and [real receipt](phase-4.6-smoke.json).

- [x] Atomic ordered batches, 256-row bound and worker chunking with a common timestamp.
- [x] Explicit approved reference, digest-pinned compact bundles and one compilation per process identity.
- [x] Exact feature schema/version/order/hash/units/config and finite-value validation.
- [x] Score version/kind/priority/nullable probability and explicit persisted/API warnings.
- [x] Actual signed path-dependent Shapley values in raw forest-vote units.
- [x] Additivity guard; calibrated probability is a separate sigmoid output.
- [x] Deterministic top-three contribution-linked reasons, including negative effects.
- [x] Missing/imputed measurements and flags described honestly; required FRP gaps withhold ML.
- [x] Baselines expose actual terms and never fabricate SHAP or probability.
- [x] Runtime performs no LLM calls, training, provider/database access or executable model loading.
- [x] Candidate construction/verification does not self-approve or activate a scorer.

Registry promotion and worker resolution of approved references remain Phase 4.7.
The Phase 4.6 library accepts a reference issued by that trusted application boundary;
the type itself is not proof of independent model review. Current worker requests
remain baseline-only. No real eligible ML model or validated severity claim exists.

## Verification

Focused synthetic tests compare exact contributions against exhaustive coalitions
on weighted small trees with repeated splits, and check constant and depth-12 trees.
They exercise calibration/null probability, signed/missing explanations, concurrent
single loading, immutable cached identity, bounds, input validation, score-schema
round trips, source export, tamper rejection, cache capacity and atomic cleanup.
The no-fit PostgreSQL-backed source package refuses candidate export.

The focused inference suite passes 39 synthetic unit cases; one additional
PostgreSQL/package case checks source-bound no-fit export refusal. Final numeric
hardening was followed by 47 passing inference/worker cases, including exact
sequential accumulation and truthful display-tolerance wording.

Strict mypy passes for 94 source files. Ruff lint/format, published schema/runtime
identity, scaffold/link/template/Git-exclusion checks, unchanged hashed locks
(54 runtime/71 development packages), pip consistency, source/wheel builds and
Git whitespace checks pass. The final installed wheel runs calibrated and raw
synthetic inference with SHAP additivity from an isolated directory without .env
or credentials, with runtime training/provider/DB/pickle access denied. Both new
CLI help commands pass. These commands construct/verify candidates; they never
approve a model. No additional runtime dependency was introduced.

The real diagnostic verified all twelve snapshots through both artifact-free
baselines, actual term sums and persisted warnings. Source-bound ML candidate
export was refused before output creation. The evaluation/training/baseline/
split/label/pipeline manifests retain their reviewed hashes, and the final-test
ledger remains unconsumed. Configured-secret scanning passed over 331 nonignored
repository files. Synthetic metrics are not real fire-model performance.

The complete PostgreSQL-backed regression passed **1,011 tests**, including 40
new Phase 4.6 cases (39 unit and one source-package integration case). HTTP
liveness/readiness/regions/status/docs/OpenAPI, operator authentication and the
unprivileged application role checks passed. One existing upstream Starlette/httpx
TestClient deprecation warning remains nonblocking. The final precision/text
hardening additionally passed the focused 47-case inference/worker suite and was
rechecked in the installed wheel. Hosted CI and independent human model review
are not asserted by these local checks.
