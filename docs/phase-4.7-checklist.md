# Phase 4.7 acceptance

Implemented the transactional registry, CLI promotion/rollback and safe worker
integration. See [operator workflow](model-registry-and-promotion.md),
[review schema](model-approval-review-v1.schema.json) and [real receipt](phase-4.7-smoke.json).

- [x] Offline `train` and `evaluate`, with explicit one-shot final access.
- [x] `promote-model --version` verifies source-bound final evidence, ranking gates,
  matching inference bytes/thresholds, feature contract, card and independent review.
- [x] Managed immutable release copies retain complete evidence and prior bundles.
- [x] Serialized atomic promotions and active/previous pointers with immutable audit.
- [x] Worker resolves approved identities and saves model ID, version, digest and review pin per run.
- [x] Jobs/runs retain their selected model across promotion; saved alerts remain immutable.
- [x] Fail-closed error codes for unavailable approval and withheld scoring, with no silent fallback.
- [x] Required FRP gaps withhold ML; optional values retain training imputation and warnings.
- [x] Transactional rollback checks retained approved artifacts without modifying historical results.
- [x] Migration `0003`, readiness, handoff exports and schema contract checks agree.

The default configuration remains explicitly baseline-only. Registry selection
requires `ACTIVE_SCORER=registry` after a valid reviewed release exists. Current
real data supports no such release; no real model was approved or activated and
the final-test ledger remains unconsumed. Review files are local operator
attestations, not an authenticated independent-review service.

## Verification

Focused acceptance covers 26 synthetic release/CLI cases and 15 PostgreSQL
registry/worker cases. Tests cover final-data and paired-ranking gates, review
bindings, source copies, atomicity/idempotence, concurrent promotion, rollback,
corrupt artifacts, retired models, missing required features, reviewed scope,
crash/resume pinning and SQL immutability. Migration upgrade/downgrade and metadata
drift checks pass. All 32 transport/demo contracts pass after centralizing the
schema revision. Synthetic approvals are confined to disposable schemas; they
are test authority stubs and establish no real model performance.

The complete PostgreSQL-backed regression passed **1,052 tests**, including 41
new Phase 4.7 cases. HTTP liveness/readiness/regions/status/docs/OpenAPI, operator
authentication and the unprivileged application role checks passed. A focused
follow-up verified that new critical ML evidence retains the actual model ID,
probability and scorer version; existing saved alerts are not rewritten. One
existing upstream Starlette/httpx TestClient deprecation warning remains nonblocking.

Strict mypy (96 source files), Ruff lint/format, schema/template equality,
scaffold/link/template/Git-exclusion checks, unchanged hashed locks (54 runtime/
71 development packages), pip consistency, source/wheel builds and Git whitespace
checks passed. Native application migration is at `0003 (head)` and `alembic check`
reports no drift. The installed wheel verified all four generic CLI help commands,
managed release integrity, synthetic approved inference, empty-registry refusal
and the real insufficient-data promotion refusal from an isolated directory
without .env or credentials and with network/DB/pickle access denied.

The real preflight kept model_versions/model_promotions/model_activation counts
at zero. Default scorer remains baseline-v1. Source/label/split/baseline/forest/
evaluation manifests are unchanged; the final-test ledger is unconsumed.
Configured-secret scanning passed over 343 nonignored repository files. Published
review schema matches the runtime model and the pending review template is
intentionally rejected as an approval. Hosted CI and authenticated independent
human model review are not asserted by these local checks.
