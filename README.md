# FireWatch AI

Backend for the IEEE ClimateChain Global Hackathon. It will collect regional
satellite thermal anomalies, enrich them with weather and climate context,
and rank hotspot clusters for analyst review.

## Current status

Phase 1.4 adds all 15 typed read APIs: run-scoped ranking, detail, summary,
GeoJSON, source freshness, replay history, saved evidence verification and
authenticated job progress. Phase 1.5 adds authenticated, bounded, idempotent
refresh/replay/anchor job submission, atomic anchor outbox writes and log redaction.
Phase 1.6 adds isolated synthetic Swagger fixtures, explicit frontend contract tests
and a generated handoff bundle. The native PostgreSQL schema and immutable publication
rules from Phase 1.3 remain in use. OpenAPI is exported as a CI artifact.
Phase 2.1 source reconnaissance is recorded: NOAA availability, a valid empty recent sample, two NOAA-20 SP historical samples, and ten complete historical-weather point checks. The candidate region remains disabled. Both seasonal NOAA-20 CSV archives (`828115` and `828116`) are delivered and imported: 17,862 distinct observations. Independent source review remains pending.
Phase 2.2 adds the reusable bounded FIRMS client and `firewatch fetch-firms`, with retry/backoff, persistent quota guards, and raw evidence manifests. See [FIRMS client](docs/firms-client.md) and [Phase 2.2 acceptance](docs/phase-2.2-checklist.md).
Phases 2.3–2.6 add normalization/revision selection, resumable seasonal imports,
48-hour refresh and a verified source handoff. Phase 3.1 adds separate weather
adapters, immutable PostgreSQL caching, offline reads and complete-hour rainfall
selection. See [weather cache](docs/weather-cache.md) and
[Phase 3.1 acceptance](docs/phase-3.1-checklist.md).
Phase 3.2 adds fixed-reference NASA POWER daily climate context, resumable annual
source caching, immutable monthly baselines and `firewatch build-baselines`.
See [climate baseline guide](docs/climate-baselines.md) and
[Phase 3.2 acceptance](docs/phase-3.2-checklist.md).
Phase 3.3 adds deterministic haversine DBSCAN snapshots, frozen source revisions,
spherical centroids, independent 6/12/24-hour activity counts and an offline
`firewatch build-snapshots` stage. See [cluster snapshots](docs/cluster-snapshots.md)
and [Phase 3.3 acceptance](docs/phase-3.3-checklist.md).
Phase 3.4 adds the strict shared features-v1 builder, 17 values/14 missing indicators,
frozen enrichment provenance and `firewatch build-features` with explicit enrichment
forks. See [feature definitions](docs/features-v1.md) and
[Phase 3.4 acceptance](docs/phase-3.4-checklist.md).
Phase 3.5 adds the standalone durable worker, slot scheduler, fenced leases,
stage recovery, FRP baseline, immutable evidence/outbox and guarded publication.
See [worker operations](docs/durable-worker.md) and [Phase 3.5 acceptance](docs/phase-3.5-checklist.md).
Phase 3.6 adds three real historical replay scenarios, portable checksummed input
bundles, exact enrichment pins and `firewatch replay --scenario <id>`.
See [deterministic replay](docs/deterministic-replay.md) and
[Phase 3.6 acceptance](docs/phase-3.6-checklist.md).
Phase 3.7 closes pipeline acceptance and exports verified Parquet features/source
memberships plus typed run/status handoff data. See
[Member 3 handoff](docs/pipeline-handoff.md) and [Phase 3.7 acceptance](docs/phase-3.7-checklist.md).
Phase 4.1 freezes the observed-FRP target and six-hour seasonal dataset design,
implements read-only historical labeling and portable offline recomputation, and
records sensor/product/overpass diagnostics. See
[target and dataset contract](docs/target-and-dataset-v1.md) and
[Phase 4.1 acceptance](docs/phase-4.1-checklist.md). The current real diagnostic
labels are excluded from training; event grouping, splits and training remain later work.
Evaluated ML and chain submission remain later modules. See the [implementation plan](FireWatchImplementationPlan.md),
[Phase 1.4 acceptance](docs/phase-1.4-checklist.md),
[Phase 1.5 acceptance](docs/phase-1.5-checklist.md), [operator API](docs/operator-api.md), and
[frontend API handoff](docs/frontend-handoff.md),
[Phase 1.6 acceptance](docs/phase-1.6-checklist.md), [Phase 2.1 acceptance](docs/phase-2.1-checklist.md), [source-access study](docs/source-access.md), and [Swagger demo](docs/demo-walkthrough.md). No real-data coverage,
trained-model accuracy or team sign-off is implied.

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
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\firewatch.exe seed-regions --config configs/regions.yaml
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
Readiness is at `http://127.0.0.1:8000/health/ready`, regions at
`http://127.0.0.1:8000/api/v1/regions`, and Swagger at
`http://127.0.0.1:8000/docs`. Business reads are deliberately public on this local
prototype (`PUBLIC_READS_ENABLED=true`); set it to false for private deployments.
Mutations and job details always require the operator bearer token; never ship it
to a browser. Production defaults private when public reads are unspecified. Liveness never queries the
database or providers. Required secrets are validated at application startup.

See [native development instructions](docs/local-development.md) for lifecycle,
backup locations, existing PostgreSQL services, configuration, tests and lock updates.
Migrations are implemented through revision `0003`. The supplied demo region
is disabled until source coverage is verified. See [schema and publication rules](docs/database-schema.md).

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
- `tests/unit`, `integration`, `contract`: foundation, security and API acceptance;
  processing end-to-end suites remain later work.
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
- [Contracts](docs/contracts.md), [database schema](docs/database-schema.md), and [data dictionary](docs/data-dictionary.md)
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

## FIRMS normalization (Phase 2.3)

Normalize a completed saved fetch with `firewatch normalize-firms --manifest <manifest.json>`.
Add `--persist --region <name>` to upsert into an existing matching region.
See the [normalization guide](docs/firms-normalization.md) for quality policies,
revision selection and frozen run inputs, and the [acceptance checklist](docs/phase-2.3-checklist.md)
for verification evidence.

## Historical FIRMS import (Phase 2.4)

`firewatch import-firms --path <local.csv> --manifest <provenance.json>` imports
trusted local data with streaming validation, bounded commits, resumable database
checkpoints and original-file preservation. Add `--dry-run` for offline inspection.
See the [operator guide](docs/firms-archive-import.md),
[acceptance checklist](docs/phase-2.4-checklist.md) and
[real seasonal inventory](docs/historical-data-inventory.json).


## FIRMS source refresh (Phase 2.5)

Run an overlapping 48-hour source stage with
`firewatch refresh-firms --region <enabled-region>`, or resume a saved refresh
using `firewatch refresh-firms --resume <refresh-id>`. The
[refresh guide](docs/firms-refresh.md) explains quota-aware checkpoints,
independent product coverage and the Member 3 normalized-record handoff.
The existing status API reports nullable source counts and source completeness.
See [acceptance](docs/phase-2.5-checklist.md) and [real smoke evidence](docs/phase-2.5-smoke.json).

FIRMS Module 2 acceptance and the offline normalized-data bundle are documented in
[the source handoff guide](docs/firms-handoff.md) and
[Phase 2.6 acceptance](docs/phase-2.6-checklist.md). Historical event groups and labels
remain unverified; source ingestion completion does not imply training readiness.

## Leakage-resistant splits (Phase 4.2)

Use `firewatch group-split-events`, `firewatch build-splits`, and
`firewatch verify-splits` for source-bound whole-event temporal partitioning.
See [partition policy and workflow](docs/leakage-resistant-splits.md) and
[acceptance evidence](docs/phase-4.2-checklist.md). The current real cohort
correctly remains baseline-only: its twelve snapshots are all off cadence.

## Baselines and simple comparison (Phase 4.3)

Both `baseline-v1` (maximum FRP) and opt-in `weighted-baseline-v1` run without
a model artifact. `firewatch compare-baselines` evaluates paired development
holdouts and conditionally fits logistic regression; the final test stays sealed.
See [scoring/comparison workflow](docs/baselines-and-comparison.md) and
[acceptance](docs/phase-4.3-checklist.md). The real sparse cohort correctly skips
supervised fitting and reports no held-out performance.

## Random Forest training and calibration (Phase 4.4)

`firewatch train-forest` performs bounded development-only search and independently
audited sigmoid calibration when data permits. `firewatch verify-forest` recomputes
the source-bound JSON artifact without executable object loading. See the
[training guide](docs/forest-training-and-calibration.md) and
[acceptance checklist](docs/phase-4.4-checklist.md). The current real cohort
correctly yields no fitted estimator; final test and model promotion remain gated.

## Evaluation and priority thresholds (Phase 4.5)

`firewatch prepare-evaluation` freezes paired comparisons and development-only
score-scale thresholds. `firewatch evaluate-final` uses a durable one-shot ledger;
`firewatch verify-evaluation` audits the same frozen artifact offline. See the
[evaluation guide](docs/evaluation-and-thresholds.md) and
[acceptance evidence](docs/phase-4.5-checklist.md). The current off-cadence cohort
reports insufficient data, no achieved metrics and a sealed final test.

## Batch inference and explanations (Phase 4.6)

`score_features()` supports explicit approved-model references, compact verified
JSON bundles, exact signed path-dependent SHAP and deterministic top-three reasons.
Both baselines expose actual score terms and missing/provisional warnings.
See [runtime contract](docs/inference-and-explanations.md) and
[acceptance evidence](docs/phase-4.6-checklist.md). No real ML artifact is approved;
registry activation remains Phase 4.7.

## Registry, promotion and rollback (Phase 4.7)

`firewatch train`, `evaluate`, `promote-model` and `rollback-model` provide the
offline release workflow. Promotion requires final-test evidence, matching
artifacts/card and independent review. `ACTIVE_SCORER=registry` selects the
transactional active pointer; default baselines remain explicit. Worker runs pin
their approved model and fail clearly on missing required data or model errors.
See [workflow](docs/model-registry-and-promotion.md) and
[acceptance](docs/phase-4.7-checklist.md). Current real data still cannot be promoted.
