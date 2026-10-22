# Pre-Phase 4 implementation fixes

Reviewed 22 October 2026 against the active FireWatch_AI checkout.

## Resolved implementation defects

- Handoff validation now binds the feature stage to its exact snapshot manifest,
  rejects extra feature-hash keys, and checks provenance region identity.
- Duplicate snapshot, revision, job and manifest run identities are rejected.
- Feature lineage must match frozen source pins, clustering input and configuration.
- JSON duplicate keys/nonfinite values and required Parquet nulls are rejected.
  Malformed package structures fail with validation errors rather than incidental
  Python exceptions.
- The refresh/replay worker does not consume anchor jobs belonging to the optional
  chain executor, including expired running anchor jobs.
- Existing 2 km DBSCAN results remain versioned. A conservative shared eligibility
  policy excludes spatially wide clusters and incomplete current FRP baselines
  from the proposed centroid-target inputs without changing saved artifacts.
- `assess-pipeline-handoff` reports per-row eligibility, exclusions, observation
  reuse and explicit outstanding dataset requirements using verified local inputs.

Seven regression cases were run against the original verifier and all demonstrated
acceptance of inconsistent packages. They are retained alongside further malformed
input, source-lineage, worker ownership and spatial/FRP boundary regressions.

## Current data and scope

The actual reviewed package still has manifest SHA-256
`6395e9ff7177c1a4c1eec3bbc708f84bbe90c293838b0ccb841a66a0865ce5c7`.
It has 12 feature rows, 1,664 memberships, three runs and three job records.
Eight rows meet the compact-target input policy; four are excluded as wide.
All 832 unique member observations are reused across snapshots. No labels,
event identities, class balance, independent holdout or predictive metrics are
claimed. The two seasonal satellite archives are imported; matching seasonal
feature/enrichment generation remains dataset construction in Phase 4.

The absence of Phase 4 labels/training/registry/explanations is planned scope,
not an unfinished Phase 1-3 defect. Human contract approval, collaborator access,
independent review and current hosted CI require external evidence. No such
approval or CI result is fabricated. Local implementation fixes do not confer
production readiness or calibrated probability claims.

## Validation

Before these fixes, the complete existing suite passed 706 tests and the real
local HTTP bootstrap/authentication checks after starting the existing native
PostgreSQL 16 instance. Earlier connection failure was local service availability.
Final post-fix validation passed:

- 735 tests passed, including 29 new regression cases; real PostgreSQL migration,
  concurrency and transactional tests ran in disposable schemas.
- HTTP liveness, readiness, region/status/Swagger/OpenAPI and operator
  authentication acceptance passed using the unprivileged application role.
- CI-scoped Ruff lint and formatting (138 files), strict mypy (72 source files),
  scaffold/link/template/Git exclusions, dependency locks and pip check passed.
- Source and wheel builds passed. The installed wheel's assessment and CLI ran
  from a temporary directory without .env or application credentials and verified
  the unchanged real package.
- Configured-secret scan passed over 241 nonignored repository files;
  git diff whitespace checks passed.
- The existing upstream Starlette/httpx TestClient deprecation warning remains
  nonblocking; it is not a test failure and no dependency migration was introduced.

See [machine-readable validation](pre-phase-4-validation.json). Hosted CI and
independent human approval are not asserted by these local checks.
