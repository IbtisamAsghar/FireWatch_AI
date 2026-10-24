# Phase 1.6 acceptance and handoff

- [x] An empty disposable PostgreSQL schema migrates through revision 0002 without
  model drift, seeds configured regions idempotently, and loads synthetic examples
  idempotently. Failure and normal shutdown remove only the owned demo schema.
- [x] Swagger demonstrates all 15 typed read operations with labeled sample data,
  prefilled UUIDs and a fixed clock. Protected job reads still require authentication.
- [x] The regular API rejects missing/wrong mutation and job tokens, recovers the
  original job on retry, returns 409 for changed input, and handles concurrent keys,
  queue limits and transactional rollback (Phase 1.5 integration acceptance).
- [x] Dedicated contract tests prove live/replay isolation, scoped detail 404s,
  historical freshness, empty completed runs, summary/list/GeoJSON consistency,
  structured errors and correlation IDs.
- [x] API contracts, operator policy, error envelope and human/machine field
  dictionaries are committed; a pure contract test rejects field-dictionary drift.
- [x] Contract tests explicitly cover aware UTC timestamps/rejected naive/epoch
  timestamps, feature/FRP units, pagination, longitude-first GeoJSON and null
  probability/missing values. Pure contract tests also run on Windows without PostgreSQL.

Deliverables: running API skeleton (18 operations), Alembic migrations, strict
versioned DTOs, CI OpenAPI export, isolated Swagger demo, frontend handoff artifact,
and unit/PostgreSQL/contract suites. API source, schema descriptions and exports
share the same response models. Sample records do not enter the application database.

The demo and handoff use repository-generated **synthetic** fixtures, not real
FIRMS/weather data, model evaluation or completed ingestion/worker execution.
The synthetic region stays disabled; every mutation is blocked in demo mode.
No provider access, real-data replay, trained scorer, chain adapter, production
throughput or human team sign-off is implied by Module 1 acceptance.

Verified locally: **186 tests passed** against native PostgreSQL, including all
contract and previous security/integrity tests. Ruff, formatting, mypy, scaffold,
lock/dependency checks and source/wheel builds passed. Real HTTP verified all 15
demo reads, Swagger, protected job access and graceful schema cleanup. The normal
API bootstrap/auth smoke checks passed as well.

Reproduce from the repository root with Python 3.12 and native PostgreSQL:

```powershell
.\.venv\Scripts\python.exe scripts/native_postgres.py start
.\.venv\Scripts\python.exe scripts/verify_bootstrap.py
.\.venv\Scripts\python.exe -m scripts.verify_demo
.\.venv\Scripts\python.exe -m scripts.export_handoff
.\.venv\Scripts\python.exe scripts/export_openapi.py
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m build --no-isolation
```

`verify_bootstrap.py` supplies the private test DSN in memory and runs all tests,
then real HTTP checks against the normal application. Standalone PostgreSQL tests
require a privately configured `FIREWATCH_TEST_DATABASE_URL` ending in `_test`.
The demo/export/verifier can derive local `firewatch_test` from the protected `.env`.
No Docker Desktop, new dependency, paid service or user provider key is needed.

The [demo walkthrough](demo-walkthrough.md) covers every read and expected results.
[Frontend handoff](frontend-handoff.md), [operator API](operator-api.md),
[shared contracts](contracts.md) and [data dictionary](data-dictionary.md) complete
the team-facing contract. CI attaches `firewatch-openapi` and
`firewatch-frontend-handoff`; runtime execution modules remain separately planned.
