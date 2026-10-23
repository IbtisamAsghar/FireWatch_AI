# Phase 5.1 acceptance: canonical evidence payload and hashing

- [x] Complete versioned evidence-v1 envelope binds run/alert/snapshot/score IDs,
  region/mode/cutoff, satellite revisions, weather values/times/product, baseline,
  features/hash, scorer/model identity, explanation, warnings and source checksums.
- [x] Fixed UTF-8/sorted-key/compact JSON, UTC microseconds, explicit 17-significant-
  digit decimal strings, null semantics and nonfinite rejection.
- [x] Explicit unordered-array sorting and positional-array preservation, with
  golden byte/hash vectors and a committed synthetic canonical example.
- [x] One canonical serialization supplies both persisted BYTEA and SHA-256 digest.
- [x] Existing database hash constraints and immutable-alert guards retain exact
  bytes; JSONB/read projections are never used as hash preimages.
- [x] Separate schema/canonicalization versions; pre-5.1 runs and existing alerts
  preserve their original format, bytes and hashes.
- [x] Pure construction/encoding performs no provider, chain, fitting or clock I/O.
- [x] Alert, optional outbox entry and evidence checkpoint commit atomically after
  scoring, with fenced retry, rollback and deduplication acceptance coverage.

See the [encoding/operator contract](canonical-evidence.md),
[published wire schema](evidence-v1.schema.json),
[synthetic canonical example](evidence-v1-example.json) and
[real cached-data acceptance receipt](phase-5.1-smoke.json).

No dependency, migration, model approval, runtime scorer selection or chain
execution change is required. Existing source/replay/ML artifacts remain frozen.
Public payload/artifact verification enhancements remain Phase 5.2.

## Verification

The complete PostgreSQL-backed regression passed **1,132 tests** in 410.40
seconds. HTTP liveness/readiness/regions/status/Swagger/OpenAPI and operator
authentication checks passed using the unprivileged application role. The final
focused evidence/worker/registry suite passed **84 tests** in 31.58 seconds,
including two final follow-up cases added after full-suite collection. Phase 5.1
adds 43 acceptance cases: 39 unit/encoding cases and four PostgreSQL cases.

The final real-data smoke completed active, quiet and degraded cached scenarios
with four canonical alerts, zero provider attempts and no chain calls. Hashes,
exact repeat-run bytes and upstream feature preimages matched. Existing
application alert bytes/hashes, run/job/score/registry counts, region enablement
and live heads were preserved. Tests additionally prove outbox-insert failure
rolls back alerts/checkpoint while retaining committed scores for retry, and
pre-5.1 runs resume their exact legacy format.

CI-scoped Ruff lint/format (207 files), strict mypy (101 source files), scaffold/
local-link/template/Git-exclusion checks, unchanged hashed dependency locks
(54 runtime / 71 development packages), dependency consistency, source/wheel
builds and Git whitespace checks passed. Configured-secret scanning passed over
371 nonignored files. The runtime-only installed wheel reproduced the committed
canonical example byte-for-byte from an isolated directory without .env, with
settings/network/database/pickle access denied. The generic legacy serializer
remains unchanged; original source/replay/ML packages were not regenerated.

One existing upstream Starlette/httpx TestClient deprecation warning remains.
Hosted CI, chain execution, operational ML validity and independent human
approval are not asserted by these local checks.
