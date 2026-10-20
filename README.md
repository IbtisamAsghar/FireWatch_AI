# FireWatch AI

Backend for the IEEE ClimateChain Global Hackathon. It will collect regional
satellite thermal anomalies, enrich them with weather and climate context,
and rank hotspot clusters for analyst review.

## Current status

Phase 1.2 provides an installable Python 3.12 package, hashed runtime/development
dependency locks, validated settings, pooled PostgreSQL sessions, the operator
CLI, and `GET /health/live`. Native PostgreSQL runs without Docker Desktop.
Ingestion, application tables/migrations, worker orchestration, ranking, ML and
chain integration are not yet implemented. See the [implementation plan](FireWatchImplementationPlan.md)
and [Phase 1.2 acceptance record](docs/phase-1.2-checklist.md).

## Start locally on Windows

Required: Git, Python 3.12, PowerShell and an editor. Run from this repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv\Scripts\python.exe scripts/native_postgres.py setup
.\.venv\Scripts\firewatch.exe --help
.\.venv\Scripts\firewatch.exe check-config
.\.venv\Scripts\firewatch.exe db-check
.\.venv\Scripts\python.exe -m uvicorn firewatch.main:app --host 127.0.0.1 --port 8000 --reload
```

The native setup helper downloads official PostgreSQL 16 binaries, stores
persistent data outside OneDrive, and generates a protected ignored `.env` with
random local secrets. Existing `.env` files are preserved. No FIRMS key is needed
until satellite ingestion is implemented. Use `setup --port 55432` if 5432 is
already occupied. No Docker Desktop, WSL, paid service or administrator Windows
service registration is required. The database runs as a per-user background process.

In another PowerShell window, call
`Invoke-RestMethod http://127.0.0.1:8000/health/live`; expect `{"status":"ok"}`.
Swagger is available at `http://127.0.0.1:8000/docs`. Liveness never queries the
database or providers. Required secrets are validated at application startup.

See [native development instructions](docs/local-development.md) for lifecycle,
backup locations, existing PostgreSQL services, configuration, tests and lock updates.
Application migrations and `alembic upgrade head` begin in Phase 1.3.

## Repository layout and ownership

- `src/firewatch/db`, `schemas`, `repositories`, `api`: Member 1, foundation/API.
- `src/firewatch/sources/firms.py`: Member 2, satellite ingestion.
- `sources/open_meteo.py`, `sources/power.py`, `processing`, `pipeline`,
  `worker.py`: Member 3, enrichment, durable jobs, replay.
- `src/firewatch/ml`: Member 4, targets, training, evaluation, explanations.
- `src/firewatch/evidence`, `contracts`, `deploy`, `.github`: Member 5,
  integrity, optional chain, CI, and delivery; Member 1 reviews shared setup.
- `configs`: reviewed non-secret configuration; region selection is pending.
- `data/sample`: small, sanitized, explicitly licensed fixtures only.
- `models`: documentation only until trusted bundles are approved.
- `tests/unit`, `integration`: bootstrap tests; contract/e2e suites expand in later phases.
- `migrations`: Member 1 owns revision numbering and integration.

## Collaboration and validation

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing shared contracts.
Use short PRs, one approval from someone other than the author, and passing
required scaffold CI and backend checks. Do not push directly to shared `main`.
[GitHub setup](docs/github-setup.md) explains how the owner enforces this.
A local passing check is not evidence of a GitHub-hosted CI run.

The Phase 1.1 check uses only Python's standard library and Git: it validates
scaffold files, local Markdown targets, Python syntax, environment-template
safety, and representative Git exclusions. It does not validate backend
behavior. Phase 1.2 adds Ruff, mypy, unit tests, real PostgreSQL transaction
tests and package/lock checks on Linux, plus Windows package tests. Migrations
and container builds are added when their corresponding phases are implemented.

## Design and handoff documents

- [Architecture decisions](docs/architecture-decisions.md)
- [Contracts](docs/contracts.md) and [data dictionary](docs/data-dictionary.md)
- [Model card](docs/model-card.md)
- [Operations runbook](docs/runbook.md)
- [Frontend handoff](docs/frontend-handoff.md)

## Data and cost policy

Prefer free NASA FIRMS/POWER services, Open-Meteo's non-commercial tier within
its terms, local PostgreSQL/storage, and local Anvil for optional chain demos.
Cache provider requests and preserve attribution. No paid service is needed
for local development; public hosting, a domain, and external backup capacity
require a separate cost decision. Provider access/coverage is not yet tested.

## Research limitation

Research and decision-support prototype only. Satellite thermal anomalies
and model priority scores can be wrong or incomplete. Do not use FireWatch AI
for emergency response, preservation of life, or protection of property.
Confirm important events with official authorities and operational fire services.

Licensed under the [MIT License](LICENSE).
