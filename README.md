# FireWatch AI

Backend for the IEEE ClimateChain Global Hackathon. It will collect regional
satellite thermal anomalies, enrich them with weather and climate context,
and rank hotspot clusters for analyst review.

## Current status

Phase 1.1 provides repository preparation and the directory skeleton.
The API, CLI, database, worker, ML pipeline, and chain adapter are **not
implemented yet**. Python module files are ownership placeholders, not
working interfaces. See [the implementation plan](FireWatchImplementationPlan.md)
and [the Phase 1.1 checklist](docs/phase-1.1-checklist.md) for completion evidence
and external steps still pending.

## Prepare Windows development tools

Required: Git, Python 3.12, Windows PowerShell, Docker Desktop with the WSL 2
backend and Linux containers, and an editor such as VS Code. Docker Desktop
must be running before infrastructure work begins. Use the same Python minor
version locally and in CI. Do not use Windows containers for the backend.

```powershell
git clone https://github.com/IbtisamAsghar/FireWatch_AI.git
Set-Location FireWatch_AI
git switch -c codex/my-task
py -3.12 --version
git --version
docker compose version
docker info --format '{{.OSType}}'
py -3.12 scripts/check_scaffold.py
Copy-Item .env.example .env
```

The Docker check must print `linux`. Copy the environment template only when
`.env` does not already exist. Replace placeholder credentials locally when
their corresponding services are implemented. Never paste secrets into
commits, screenshots, issues, or logs. FIRMS credentials are obtained from
[NASA's free MAP_KEY registration](https://firms.modaps.eosdis.nasa.gov/api/map_key/).

Dependency installation, package metadata/lock files, Compose services,
migrations, `firewatch --help`, and `/health/live` belong to Phase 1.2.
No install or service-start commands are advertised as working before then.

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
- `tests/unit`, `integration`, `contract`, `e2e`: future test suites.
- `migrations`: Member 1 owns revision numbering and integration.

## Collaboration and validation

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing shared contracts.
Use short PRs, one approval from someone other than the author, and passing
`Repository checks / scaffold` CI. Do not push directly to shared `main`.
[GitHub setup](docs/github-setup.md) explains how the owner enforces this.
A local passing check is not evidence of a GitHub-hosted CI run.

The Phase 1.1 check uses only Python's standard library and Git: it validates
scaffold files, local Markdown targets, Python syntax, environment-template
safety, and representative Git exclusions. It does not validate backend
behavior. Phase 1.2 and Module 5 expand CI to Ruff, mypy, pytest, migrations,
and container builds as those components become available.

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
