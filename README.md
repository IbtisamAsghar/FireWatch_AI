# FireWatch AI

Backend for the IEEE ClimateChain Global Hackathon. It collects regional
satellite thermal anomalies, enriches them with weather and climate context,
and ranks hotspot clusters for analyst review.

## Current status

This branch delivers the Python backend, historical source data, replay bundles,
a consistent PostgreSQL dump, and the reviewed `forest-independent-2025-v2` model.
There is no frontend application; Swagger and the typed API handoff support frontend
integration. The implementation includes ingestion, enrichment, durable workers,
model evaluation/registry, evidence verification and optional chain anchoring.

The delivered review approves the model for **historical northern-California
NOAA-20 SP replay only**. The original-machine activation is recorded in
[the readiness receipt](docs/local-real-readiness-receipt.json); a new clone must
restore/configure its database before that state exists locally. Default clean
installations use `baseline-v1`. Model scores are uncalibrated ranking values;
`escalation_probability` remains null. The saved 2025 forest AP is 0.109701,
versus 0.050746/0.055594 for the two baselines and 0.139349 for logistic.
The delivered forest uses satellite/activity splits only; additional weather or
climate predictive skill is not established.

Start with the [current model card](docs/model-card.md),
[fresh historical restore guide](docs/local-real-readiness.md), and
[delivery/rebuild procedure](docs/devtalha-delivery.md).
Live NRT/new-region generalization, end-user login, production HTTPS and
operational deployment acceptance remain separate work. Historical phase notes
below describe the state at each milestone, not the current release status.

## Start locally on Windows

Required: Git, Python 3.12, PowerShell and an editor. Run from this repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv\Scripts\python.exe scripts/prepare_local.py
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
until satellite ingestion is implemented. The isolated instance defaults to port 55432; choose a separate `--home`
and `--port` to provision another instance without reusing an existing cluster. No Docker Desktop, WSL, paid service or administrator Windows
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
- `models`: checksum-pinned managed historical model release and review evidence.
- `tests/unit`, `integration`, `contract`: foundation, security and API acceptance;
  pipeline, model, replay and integrity acceptance suites are implemented.
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

## Historical implementation milestones

The following notes preserve phase-specific acceptance context. Their earlier
statements about missing data, unapproved models or future modules are historical.
Use Current status above and the current model card for the delivered release.

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
registry activation requires the Phase 4.7 release gates.

## Registry, promotion and rollback (Phase 4.7)

`firewatch train`, `evaluate`, `promote-model` and `rollback-model` provide the
offline release workflow. Promotion requires final-test evidence, matching
artifacts/card and independent review. `ACTIVE_SCORER=registry` selects the
transactional active pointer; default baselines remain explicit. Worker runs pin
their approved model and fail clearly on missing required data or model errors.
See [workflow](docs/model-registry-and-promotion.md) and
[acceptance](docs/phase-4.7-checklist.md). Current real data still cannot be promoted.

## Acceptance and baseline handoff (Phase 4.8)

`firewatch export-baseline-release` packages matching source-bound evaluation
and baseline comparisons, the training manifest, model card, explicit absent
ML bundle and portable reproduction commands. `firewatch verify-baseline-release`
verifies the complete package against a trusted manifest SHA-256.
See [acceptance and operator commands](docs/phase-4.8-checklist.md) and the
[current model card](docs/model-card.md). The documented baseline release is
the supported Phase 4 deliverable while real data remains insufficient for ML.

## Historical training-data backfill

`firewatch backfill-training-data` rebuilds the frozen six-hour seasonal dataset
from cached sources without publishing application runs. Verified chunks support
resume, offline feature/label recomputation and merging into the existing training
input contract. `fill-backfill-weather` provides bounded historical cache filling.
The first complete job produced **1,819 eligible rows**, fixing the original
zero-row blocker; most weather remains missing and explicitly flagged.
See [workflow and current data limitations](docs/historical-training-backfill.md).

## Canonical alert evidence (Phase 5.1)

New runs pin `evidence-v1` / `evidence-json-v1`: a complete source-bound payload
with decimal strings, UTC timestamps and exact immutable bytes/SHA-256.
Alert and optional anchor-outbox publication share one transaction; no chain
access is needed. Existing alerts and already-admitted runs keep their original
encoding. See [encoding and compatibility](docs/canonical-evidence.md) and
[acceptance](docs/phase-5.1-checklist.md).

## Evidence verification and artifact audit (Phase 5.2)

Alert detail and `/alerts/{alert_id}/verify` support opt-in
`verify_artifacts=true` to check the exact source/model files pinned in saved
evidence. Payload, artifact and optional anchor results remain independent;
missing files and RPC outages are distinct from checksum mismatches.
`firewatch verify-alert --alert-id UUID --artifacts` provides a read-only CLI;
`--payload-copy FILE` demonstrates tampering without changing saved evidence.
See [verification and limits](docs/evidence-verification.md) and
[acceptance](docs/phase-5.2-checklist.md). No chain execution or model activation
is added.

## Optional Solidity registry (Phase 5.3)

The append-only `AlertRegistry` accepts exact SHA-256 commitments from one
immutable writer, rejects conflicting overwrites and makes identical retries
idempotent. Native checksum-pinned Forge tests and a disposable loopback Anvil
deployment are available through `python scripts/contract_tools.py install`,
`test` and `smoke`. See [contract tooling](contracts/README.md),
[wire encoding and deployment](docs/alert-registry.md), and
[acceptance](docs/phase-5.3-checklist.md). The local demonstration does not enable
application anchoring or provide independent public notarization; durable
submission and RPC integration remain Phase 5.4.

## Durable anchoring (Phase 5.4)

`firewatch anchor-worker` runs a separate PostgreSQL-serialized signer. It commits
transaction bytes/hash before broadcast, recovers the same identity after crashes,
tracks canonical receipts/confirmations and monitors reorgs. `anchor-status`
reports safe progress by job/outbox ID; `retry-anchor` safely requeues eligible
failures. API verification now performs real read-only chain checks without
requiring the API process to hold a signing key. Chain and pipeline admission
have independent queue capacity. See [configuration and recovery](docs/durable-anchoring.md)
and [acceptance](docs/phase-5.4-checklist.md). Local `.env` remains chain-disabled
unless explicitly configured; no public-chain deployment or ML activation is implied.

## CI and integration quality (Phase 5.5)

Standard CI runs without provider keys and blocks accidental external network
access during pytest. Full pipeline tests now reach the frontend API and evidence
verification with cached/missing enrichment and a deterministic synthetic model.
Release-tag/PR checks add dependency advisories, redacted Git/worktree secret scans,
a non-root Linux container build, retained test/coverage reports and public build
metadata. PyArrow is updated to the fixed 23.0.1 release. Live provider checks are
separate and explicitly opt-in. See [workflow and reproduction](docs/ci-and-integration.md)
and [acceptance and validation limits](docs/phase-5.5-checklist.md).

## Local full-stack containers (Phase 5.6)

With Docker running Linux containers and Compose installed, use the existing
Python environment from the repository root:

```powershell
.\.venv\Scripts\python.exe -m scripts.compose_stack init
.\.venv\Scripts\python.exe -m scripts.compose_stack up --build
.\.venv\Scripts\python.exe -m scripts.compose_stack status
```

Open `http://127.0.0.1:8000/docs`. The helper creates separate protected
`.env.compose` credentials, applies release migrations/seeding, and starts
PostgreSQL/API/worker with persistent storage and health checks. Native `.env`
and database remain separate. No synthetic observations are loaded; regions
remain disabled until deliberately enabled for real-data ingestion.
`python -m scripts.compose_stack down` preserves volumes. See
[startup, real-data use, artifact transfer and optional services](docs/container-stack.md)
and [verification limits](docs/phase-5.6-checklist.md).
