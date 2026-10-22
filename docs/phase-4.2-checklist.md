# Phase 4.2 acceptance: leakage-resistant splits

- [x] Full-season physical observations grouped by frozen 4 km/48 h connected
  components; ambiguous 8 km/72 h neighbors and large periods inspected.
- [x] Hash-bound local review conservatively merges ambiguous periods; reviewed
  event IDs remain partition metadata outside the 31 model inputs.
- [x] Five chronological roles, whole-event cross-boundary exclusion and inclusive
  48-hour purge on each side implemented, with disjoint time-support assertions.
- [x] Three expanding event-disjoint development folds and sufficiency gates
  implement sparse-data fallback without consuming the final holdout.
- [x] Training-only imputation/scaling and development selectors prevent final-test
  access by subsequent hyperparameter searches. No estimator fitted in this phase.
- [x] Target, feature definitions, baseline reference years, cohort and policy
  hashes frozen before final evaluation.
- [x] Portable package saves memberships, exclusions, class/event/row counts,
  fold identities, reviews, dataset checksum and original label provenance.
- [x] Offline verification rebuilds labels, graph and splits; path, hash, inventory,
  rehashed tampering, output preservation and staging failure cases covered.
- [x] Current real cohort correctly selects baseline-only fallback and preserves
  `training_ready=false` and `final_test_status=sealed`.

See [policy and operator guide](leakage-resistant-splits.md),
[split policy](../configs/splits-v1.yaml), [event policy](../configs/event-groups-v1.yaml),
and [reproducible real receipt](phase-4.2-smoke.json).

The private final package is `data/splits/phase-4.2-final`, manifest SHA-256
`cca85bb36cc4a207a152b38686f685de961a8b6ebb9c9ddaa807547d17d08264`.
It verifies 17,862 NOAA-20 SP observations, 1,070 raw thermal-activity components,
340 neighboring pairs, and 772 conservatively reviewed partition groups. This is
local technical review; independent human incident verification is not asserted.
Every original diagnostic row is retained in membership with exclusion reasons.
All twelve rows are off cadence, four also have unknown spatially wide targets,
and all five partitions contain zero eligible rows. No supervised fit, calibrated
probability, held-out metric or final-test evaluation is claimed.

Phase 4.2 is implemented, including its specified baseline-only fallback. Phase
4.3 baseline implementation can proceed. Supervised model training requires a new
seasonal cadence-aligned feature/label cohort with adequate independent events
and classes. Preserve the current feature and target contracts while generating
that cohort, and rebuild a new reviewed split package before fitting.

## Validation

867 tests passed on the final implementation, including 66 new Phase 4.2 cases:
58 unit cases and eight PostgreSQL-backed portable-package cases. Full HTTP
liveness/readiness/regions/status/docs/OpenAPI smoke and operator authentication
passed using the unprivileged application role. One existing upstream
Starlette/httpx TestClient deprecation warning remains nonblocking.

CI-scoped Ruff lint/format, strict mypy (78 source files), scaffold/link/template/
Git-exclusion checks, unchanged hashed dependency locks (44 runtime/62 development
packages), pip dependency checks, source/wheel builds and Git whitespace checks
passed. The final installed wheel recomputed the real package from an isolated
working directory without .env or application credentials. Configured-secret
scanning passed over 278 nonignored repository files and 16 private split files.
The reviewed original pipeline, labels and seasonal-source manifest hashes remain
unchanged. No database migration, additional dependency or model promotion.

Hosted CI and independent human review are not asserted by local checks.
