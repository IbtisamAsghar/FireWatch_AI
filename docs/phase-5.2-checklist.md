# Phase 5.2 acceptance: evidence verification and audit behavior

- [x] Exact saved-byte SHA-256 comparison remains independent of JSON projections
  and legacy encoding; local match/mismatch are reported without rewriting evidence.
- [x] Confirmed chain digests are read only through the trusted adapter using saved
  destination identity. Stored receipt metadata alone cannot establish a match.
- [x] Unanchored, pending, matching, mismatching and unavailable anchor outcomes
  stay distinct; timeout/malformed-response tests never turn outages into mismatches.
- [x] Opt-in HTTP/CLI audits verify pinned source bytes and compact model bundle
  manifest/member checksums, with distinct partial/missing/unsafe/oversize results.
- [x] Referenced UUIDs resolve through server metadata; public results expose no
  paths, raw exceptions or credentials. Traversal/symlink/junction and byte/reference
  bounds constrain filesystem access. No source fetching or estimator loading occurs.
- [x] Local privileged-rewrite, local-chain independence and scientific-validity
  limits are explicit in API responses and operator/frontend documentation.
- [x] Copied synthetic payload tampering produces `local_mismatch` while original
  saved bytes/digests stay unchanged. CLI tests enforce read-only transactions.
- [x] Additive response/query types, generated field dictionary and runtime CLI
  help are included in the existing frontend/CI handoff.

No migration, dependency lock, source/model artifact, model promotion, active
scorer or chain-execution change is required. Phase 5.3 Solidity and Phase 5.4
transaction submission remain separate work.

## Reproduce

Use Python 3.12 and the locked development environment. Point
`FIREWATCH_TEST_DATABASE_URL` at a disposable PostgreSQL database ending `_test`.
Tests create/drop only generated schemas.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m ruff format --check src tests
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe scripts/check_scaffold.py
.\.venv\Scripts\python.exe scripts/check_locks.py
.\.venv\Scripts\python.exe scripts/export_openapi.py --field-dictionary docs/api-field-dictionary.json
.\.venv\Scripts\firewatch.exe verify-alert --help
```

The focused API/evidence/CLI regression passed 37 tests on an isolated native
PostgreSQL 16 cluster. The initial unit/encoding/API-contract run passed 72 tests;
one real-symlink test was skipped because this Windows account cannot create
symbolic links. Traversal/outside-root/directory cases passed independently.
The complete regression passed **1,169 tests**, with the same one Windows-symlink
skip, in **458.98 seconds** on PostgreSQL 16. The final artifact-audit suite passed
**30 tests**, with one skip, including four guard/read-limit cases added after
full-suite collection. Mocked symlink and Windows-junction guards both passed.

Ruff lint/format (186 Python files), strict mypy (101 source files), scaffold,
local-link and lock consistency checks passed. Runtime/development locks remain
unchanged (54/71 packages). Source and wheel builds, dependency consistency and
runtime-only installed-wheel checks passed. The wheel reproduced exact-byte
match/mismatch and missing-reference results with settings loading, network,
database creation, model fitting and pickle access denied. Its `verify-alert`
help works without startup configuration. Configured-secret scanning passed;
the local `.env` remains Git-ignored. See the [acceptance receipt](phase-5.2-smoke.json).

One existing upstream Starlette/httpx TestClient deprecation warning remains.

Real cached replay/model bundles are absent from this checkout, so this phase's
new behavior is verified using explicitly synthetic fixtures against a real
PostgreSQL database. Existing real-data Phase 5.1 receipts remain unchanged;
no new real-data acceptance, hosted CI or chain execution is asserted.

See [the full verification contract](evidence-verification.md).
