# Architecture decisions

Status: accepted project direction for implementation, not deployed behavior.
Source of truth: [implementation plan](../FireWatchImplementationPlan.md).

## ADR-001: Regional Python backend

Use Python 3.12, FastAPI/Uvicorn, Pydantic v2/pydantic-settings, and Typer.
One selected bounding box keeps data volume and provider requests bounded.
API handlers read completed runs or queue work. Frontend, accounts, dispatch,
global processing, and distributed model serving remain out of scope.

## ADR-002: Shared durable state

PostgreSQL 16 with SQLAlchemy 2, psycopg 3, and Alembic supports concurrent
API/worker use and relational constraints. Start with ordinary coordinates
and bounded queries; defer PostGIS and SQLite. Member 1 integrates migrations.
Deduplicate observations across overlapping region requests while retaining
all source lineage. Raw assets and models use persistent filesystem storage
with recorded hashes; the database stores metadata and references.

## ADR-003: One durable worker

Use PostgreSQL jobs, transactional claims, leases, heartbeats, bounded retries,
and idempotent stages. Run the scheduler outside Uvicorn. Publish completed
runs atomically; preserve the last completed run after a refresh failure.
Defer Redis/Celery until measured workload justifies them.

## ADR-004: Time and source provenance

Use NOAA-20/21 FIRMS detections, cached Open-Meteo model weather, and precomputed
NASA POWER climate context. Preserve observation, valid, retrieval, cutoff,
and scoring times separately. Historical weather is mandatory for replay.
Archive/reanalysis evaluation is retrospective unless availability at the
original time is proven. Use explicit source/product versions, UTC, units,
nulls/quality flags, checksums, and sanitized manifests. A 15-minute refresh
does not imply a new satellite observation every 15 minutes.

## ADR-005: Baseline before ML

Use reproducible haversine DBSCAN snapshots, initially 2 km over the preceding
six hours. Share past-only features across live/train/replay. First rank by
documented baselines; compare logistic regression and Random Forest only
when event-separated historical data supports training. The target is later
observed FRP strengthening within 24 hours, not physical fire severity.
Publish probability only with validation/calibration evidence. SHAP explains
the underlying tree output; it does not establish causation. No LLM service
is needed. Never use future records in features or final-test data for tuning.

## ADR-006: Local evidence first

Canonical UTF-8 JSON bytes and SHA-256 provide reproducible integrity checks.
Save payloads immutably and version canonicalization. Optional web3.py,
Solidity, and Foundry/Anvil anchor the same digest through an outbox. Chain
failures never block scoring. A local chain is a demonstration, not independent
public notarization; local hashes do not protect against rewriting both data
and hash by a privileged attacker. Integrity does not prove a fire is real.

## ADR-007: Free local services and simple delivery

Prefer free source access within terms, PostgreSQL, filesystem storage, and
optional local Anvil. Deploy one Linux Compose host with persistent volumes
and Caddy HTTPS; larger training stays offline. Public hosting/domain/backup
costs require a separate decision. Pin compatible dependencies in Phase 1.2;
Python 3.12 and PostgreSQL 16 are compatibility choices, not newest-version
claims. Keep runtime/dev locks, trained artifacts, and inference compatible.

## ADR-008: Review, CI, and ownership

Five module owners work against agreed interfaces. Require short PRs, one
independent approval, passing CI, and no direct shared-main changes. Start
with dependency-free scaffold CI; expand it alongside implementation. No
backend tests, deployment, or hosted CI success are claimed by scaffolding.
