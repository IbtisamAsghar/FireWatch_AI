# Phase 4.1 acceptance: target and dataset design

- [x] Frozen target specification and schemas precede training, with prototype
  settings bound to a SHA-256 and rejected changes under the same version.
- [x] Strict observed-FRP positive threshold and complete-follow-up negative rule
  implemented using the existing 2 km haversine convention and `(T,T+24h]`.
- [x] Negatives without later observations have `unobserved_followup=true`;
  unknowns preserve null labels and explicit exclusion reasons.
- [x] Incomplete/end-of-archive days, rejected/failed sources, known outages,
  invalid/missing/partial baselines and source bbox boundaries are handled.
- [x] Delivery completeness is distinct from cloud-free observation coverage;
  negative sensitivity strata and release analysis requirements are documented.
- [x] Six-hour UTC cadence and two frozen study periods produce 1,472 candidate
  cutoffs. Off-cadence diagnostic rows cannot enter the training cohort.
- [x] Sensor/product, confidence, spatial/time overlap and observation-hour effects
  evaluated descriptively on real available sources; unsupported pooling refused.
- [x] Read-only PostgreSQL selection, portable provenance and offline exact label
  recomputation verified against the real current handoff.
- [x] Current features, runs, scores, alerts, live heads and region enablement are
  preserved. No new dependency, migration, training, partitioning or model promotion.

See [target/operator guide](target-and-dataset-v1.md), [frozen target](../configs/target-v1.yaml),
[dataset design](../configs/target-dataset-v1.yaml) and [real smoke](phase-4.1-smoke.json).

The real diagnostic package contains twelve off-cadence rows: eight unobserved
negative labels and four unknown spatially wide rows. Its training readiness is
false. This phase freezes the design and implements labeling; it does not claim
that seasonal features, reviewed independent events, balanced classes, split
acceptance or supervised model readiness already exist.

## Final validation

801 tests passed, including 66 new Phase 4.1 cases: 52 pure unit cases and 14
PostgreSQL/package integration cases. Full HTTP liveness/readiness/status/read/docs
and operator authentication smoke passed with the unprivileged application role.
An existing upstream Starlette/httpx TestClient deprecation warning remains nonblocking.

CI-scoped Ruff lint/format, strict mypy (74 source files), scaffold/link/template/
Git-exclusion checks, unchanged hashed locks (44 runtime/62 development packages),
pip dependency checks and source/wheel builds passed. The installed wheel verified
the actual private label package and planned the 1,472 cutoffs from an isolated
working directory without .env or application credentials. Configured-secret scanning
passed over 259 nonignored repository files and eight private label-package files.
Git diff whitespace checks passed. No new dependency or database migration was needed.

The reviewed final private package is `data/labels/phase-4.1-final`, with manifest
SHA-256 `15e7bf0aa34e2521e89b84fc0a3bf460753ccf47b84229594ad48719611cbdae`.
All twelve results recompute exactly from frozen current features and separately
pinned follow-up. The original pipeline handoff digest remains unchanged.

Hosted CI and independent human review are not asserted by local checks.
