# FireWatch AI: Complete Backend Implementation Plan

Version: 1.0 | Prepared: 17 October 2026 | Team: 5 members

This is an implementation roadmap, not an existing working backend. Commands referencing project modules describe the interfaces the team will implement. All checkboxes start incomplete. Frontend implementation is excluded.

## 1. What we are building

FireWatch AI is a backend service that collects satellite thermal-anomaly detections, groups nearby detections, adds weather and historical climate context, and ranks hotspot clusters for environmental analyst review. It explains each ranking and preserves the evidence behind important alerts.

The central question is: **Which currently detected hotspot clusters should an analyst investigate first, based on the likelihood of stronger satellite-observed activity in the next 24 hours?**

It does not discover fires before satellites detect them, confirm every anomaly as a wildfire, predict exact fire boundaries, or provide emergency instructions. A high score is a research priority, not an official emergency classification.

### Expected backend outcome

By completion, another developer should be able to:

1. Start the backend from a clean clone using documented commands.
2. Fetch recent real FIRMS detections for one configured region.
3. Import historical satellite data and matching historical weather.
4. Create reproducible hotspot snapshots and feature records.
5. Train and evaluate a versioned model against simple baselines.
6. Request ranked hotspots, explanations, source status, and GeoJSON through REST APIs.
7. Run historical replay without depending on live services.
8. Verify whether an alert's saved evidence has changed.
9. Deploy the API and background processing with persistent storage and HTTPS.
10. Hand an OpenAPI specification and sample responses to the future frontend team.

### Backend features

| Feature | Required result |
|---|---|
| Regional live ingestion | Scheduled refresh of NOAA-20/NOAA-21 detections |
| Historical ingestion | Validated archive import with provenance |
| Spatial processing | Reproducible clusters and past-activity features |
| Weather enrichment | Time-matched temperature, humidity, wind, and precipitation |
| Climate context | Cached seasonal temperature and rainfall reference values |
| Prioritization | Transparent baseline first, evaluated ML model second |
| Explainability | Top contributing factors tied to the exact scoring inputs |
| REST API | Ranked results, details, summaries, GeoJSON, jobs, health |
| Replay | Isolated historical runs with explicit historical timestamps |
| Evidence integrity | Immutable payload, SHA-256, verification endpoint |
| Optional blockchain | Authorized alert anchoring on a local EVM chain or testnet |
| Operations | Retries, source status, durable jobs, logs, backups, CI/CD |

## 2. Scope and implementation decisions

### Included in this build

- One selected region, represented by a bounding box.
- A single backend repository and shared relational database.
- Python API, command-line tools, and background worker.
- Real live data plus real historical replay data.
- One supervised binary classifier if the dataset supports it.
- Baseline scoring that works before model training is complete.
- Locally verifiable evidence as a required feature.
- Optional blockchain integration after the core pipeline passes its acceptance checks.
- API authentication for operator actions and strict separation of public and operator responses.
- Docker Compose deployment on one Linux server.

### Deferred

- All frontend code, maps rendered in a browser, UI design, and mobile apps.
- Streamlit, React, Folium UI, or frontend chart libraries.
- User accounts, organizations, billing, SMS/email alerts, and emergency dispatch.
- Global coverage, Kubernetes, distributed model serving, or a custom token.
- Fuel/terrain simulation, exact spread prediction, and satellite-image deep learning.
- Automatic retraining or automatic promotion of a newly trained model.
- Public arbitrary file uploads and user-supplied provider URLs.

### Important changes from the initial concept

1. Use FastAPI instead of Streamlit for this backend phase. The future frontend consumes REST APIs.
2. Use PostgreSQL from the beginning because five members, an API, and a worker need a consistent schema and concurrent access. SQLite is unnecessary in the main path.
3. Start with ordinary latitude/longitude columns and bounded regional queries. PostGIS is a future upgrade, not a prerequisite.
4. Run a single durable database-backed worker. Do not add Redis/Celery until workload justifies them.
5. Resolve the original target ambiguity: the initial ML target is **later observed FRP strengthening**, requiring a later detection. Persistence counts are features; they are not a second incompatible target.
6. Do not advertise an unvalidated Random Forest output as a reliable probability.

## 3. Chosen technology stack

| Layer | Choice | Purpose |
|---|---|---|
| Runtime | Python 3.12 | Shared API, ingestion, processing, and ML language |
| API | FastAPI + Uvicorn | REST endpoints and generated OpenAPI documentation |
| Validation/configuration | Pydantic v2 + pydantic-settings | Typed requests, responses, environment configuration |
| Database | PostgreSQL 16 | Durable records, run state, jobs, provenance, alerts |
| Database access | SQLAlchemy 2 + psycopg 3 | Transactions and repository functions |
| Migrations | Alembic | Reproducible database schema changes |
| HTTP clients | httpx + tenacity | Timeouts, rate limiting, retries |
| Data processing | pandas + NumPy + PyArrow | Cleaning, features, Parquet training artifacts |
| Spatial clustering | scikit-learn DBSCAN with haversine distance | Group nearby detections correctly in kilometers |
| ML | scikit-learn RandomForestClassifier | Initial supervised model |
| ML comparison | Logistic regression + simple FRP/rule baselines | Demonstrate whether ML improves prioritization |
| Explanations | SHAP for the tree model | Per-record feature contributions |
| Artifacts | joblib + JSON manifests + SHA-256 | Versioned trusted model and data bundles |
| CLI | Typer | Import, refresh, replay, train, evaluate, promote, verify |
| Scheduling | One dedicated Python worker polling PostgreSQL jobs | Durable refresh and replay execution |
| Verification | hashlib + canonical JSON | Evidence integrity |
| Optional chain | web3.py + Solidity + Foundry Anvil/Forge | Local EVM demonstration and contract tests |
| Testing | pytest + pytest-cov + respx | Unit, integration, provider, and API tests |
| Quality | Ruff + mypy | Formatting, linting, and typed contracts |
| Delivery | Docker + Docker Compose + GitHub Actions | Repeatable builds and CI |
| Public HTTPS | Caddy reverse proxy | TLS termination for the deployed API |

Use compatible releases, test them together, and commit an exact dependency lock. These are deliberate project choices, not claims that each listed major version is the newest. Use the same Python and dependency versions for training and inference.

## 4. Data sources, services, access, and cost

Official sources were consulted on 17 October 2026. Recheck terms and availability when implementation starts.

| Source | Data used | Access and cost | Usage in project |
|---|---|---|---|
| NASA FIRMS recent Area API | Coordinates, UTC acquisition time, FRP, brightness, confidence, sensor | Free MAP_KEY; provider transaction limits apply | Live collection |
| NASA FIRMS archive | Historical detections | Earthdata/email access; archive requests can require preparation | Training and replay |
| Open-Meteo Forecast API | Recent/current hourly weather estimates | Free non-commercial service within published limits | Live enrichment |
| Open-Meteo Historical Forecast API | Archived model weather matching historical dates | Check selected model/date coverage and current free-service terms | Preferred training/replay weather |
| Open-Meteo Historical Weather API | Reanalysis weather | Same terms check; available data depend on dataset | Explicitly marked research fallback |
| NASA POWER Daily API | Historical temperature and precipitation | Free NASA data service | Precomputed seasonal context |
| Local EVM chain | Test alert evidence transactions | No public-chain gas purchase | Optional blockchain demo |
| Server/domain | Hosting and HTTPS address | Local execution can be free; public hosting/domain may cost money | Deployment |

Open-Meteo's free API is for non-commercial use, requires attribution, and has daily/hourly/minute limits. Cache aggressively and budget below the published ceilings. A non-commercial educational prototype fits the intended plan; a later commercial launch needs a fresh terms review. [S4]

### Live does not mean instantaneous

- Refreshing every 15 minutes is our polling policy, not a promise that satellites produce a new detection every 15 minutes.
- Always return acquisition time, weather valid time, retrieval time, and scoring time separately.
- Climate baselines are historical context and should be precomputed.
- Replay uses historical dates everywhere. It must never enrich a past fire with today's weather.
- Historical archives or reanalysis retrieved later are retrospective inputs. Unless original release times are known and enforced, report evaluation as retrospective; do not call it a strict operational backtest.

### Provider request patterns

```text
# FIRMS: placeholders are replaced server-side; never expose MAP_KEY in API responses/logs.
https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_NOAA20_NRT/{WEST,SOUTH,EAST,NORTH}/1
https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/VIIRS_NOAA21_NRT/{WEST,SOUTH,EAST,NORTH}/1

# Open-Meteo live hourly data: explicit UTC and units.
https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,precipitation&past_days=2&forecast_days=1&timezone=GMT&wind_speed_unit=ms

# Historical forecast: select the historical date range and verify model coverage first.
https://historical-forecast-api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}&start_date={YYYY-MM-DD}&end_date={YYYY-MM-DD}&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_gusts_10m,precipitation&timezone=GMT&wind_speed_unit=ms

# NASA POWER: example baseline query, not a sufficient multi-year baseline by itself.
https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M,PRECTOTCORR&community=AG&longitude={LON}&latitude={LAT}&start=20150101&end=20151231&format=JSON&time-standard=UTC
```

Treat these as client implementation patterns. Validate actual parameter availability, returned units, time conventions, sentinels, and source coverage before importing a large dataset. Query FIRMS data availability rather than assuming every sensor/date combination exists. The current Area API documents a 1-5 day range; archive import is the reliable path for longer training histories. [S1-S3]

## 5. Architecture and contracts agreed by all members

```text
FIRMS recent/archive ----------> raw assets -> normalized detections
Open-Meteo live/archive -------> weather cache ----------+
NASA POWER -------------------> climate baselines ------+
                                                        v
PostgreSQL jobs -> dedicated worker -> snapshots -> feature sets
                                                        |
                           offline labels/training <-----+
                                                        v
                                  baseline/model -> scores -> evidence
                                                        |
                                  FastAPI reads <--------+--> optional EVM anchor
                                                        |
                                      future frontend, Swagger, CLI
```

The API queues expensive work and reads completed data. It does not make a weather request for every GET or train a model inside an HTTP handler. A failed refresh does not overwrite the last completed run.

### Team ownership: exactly five modules

| Module/member | Owns | Main handoff |
|---|---|---|
| 1: Backend foundation and API | Project setup, schemas, migrations, repositories, API contracts, auth | Stable database and REST interfaces |
| 2: Satellite ingestion | FIRMS live/archive clients, normalization, provenance, data quality | Validated detection batches |
| 3: Enrichment and processing | Weather, POWER, clustering, features, worker jobs, replay | Reproducible feature snapshots |
| 4: ML and explanations | Labels, splits, baselines, training, calibration, scoring, SHAP | Versioned scoring interface and evaluation |
| 5: Integrity and delivery | Evidence, optional blockchain, CI, integration QA, containers, deployment | Verified deployable backend |

Members may review each other's work. Ownership prevents conflicting implementations; it does not mean working in isolation. Module 1 owns migration numbering and merges model definitions contributed by the others.

### Shared data conventions

- UTC ISO-8601 timestamps with `Z` in JSON; PostgreSQL `timestamptz` internally.
- `acquired_at`: satellite observation time; `valid_at`: weather time; `retrieved_at`: download time.
- `as_of`: feature cutoff T; `created_at`: persistence time; `scored_at`: model execution time.
- Temperature Celsius; wind meters/second; precipitation millimeters; FRP megawatts.
- GeoJSON uses `[longitude, latitude]`; haversine uses radians internally.
- VIIRS confidence preserves the raw provider class, with a separately documented normalized category. Never invent a percentage.
- Missing values are null with quality flags. They are never silently changed to zero.
- IDs are UUIDs unless a deterministic deduplication key is explicitly specified.
- Every output has `run_id`, `mode`, `schema_version`, and relevant model/feature versions.
- `mode` is `live` or `replay`. Every query is isolated by run; replay never becomes the latest live run.
- All methods and schemas below are contracts to implement and version together.

### Core tables

| Table | Required fields and constraints |
|---|---|
| `regions` | id, name, bbox, enabled; valid coordinate ranges and west < east/south < north |
| `source_assets` | id, provider, source/product, sanitized request metadata, retrieval time, local/object path, content hash, format, processing version |
| `ingestion_batches` | id, region/product/window, status, asset_id, fetched/accepted/rejected counts, failure reason |
| `detections` | id, dedup_key unique, asset/batch lineage, product, sensor, lat/lon, acquired_at, FRP, brightness, raw/normalized confidence, daynight |
| `weather_records` | cell key/coordinates, provider/model/product, valid_at, retrieved_at, units, values, quality flags, asset_id; unique source/cell/time/version |
| `climate_baselines` | cell, source, reference years, month, statistic definitions, values, sample counts, version, asset_id |
| `jobs` | id, type, payload, idempotency_key unique, status, attempts, available_at, lease/heartbeat timestamps, result run_id, error |
| `runs` | id, region, mode, as_of, status, config hash, input asset manifest, completeness, started/completed timestamps |
| `cluster_snapshots` | id, run_id, centroid, member count, FRP aggregates, latest detection, cluster algorithm/version |
| `cluster_members` | snapshot_id, detection_id; unique pair |
| `feature_sets` | snapshot_id, feature schema version, feature JSON, missing flags, lineage, feature hash |
| `model_versions` | id/version, artifact path/hash, feature version, training manifest, metrics, target definition, status |
| `scores` | snapshot_id, scorer/model version, numeric score, score_kind, calibrated probability nullable, priority, explanation, scored_at |
| `alerts` | id, snapshot/score reference, canonical payload text/bytes, original hash, evidence schema version, created_at; immutable |
| `anchor_outbox` | alert_id/network unique, status, attempts, available_at, tx hash, receipt metadata, error |
| `anchors` | alert_id, chain_id, contract address, transaction/block hash and height, anchored digest, confirmation status |

Use foreign keys and indexes on `(region_id, acquired_at)` through ingestion lineage or an explicit detection region association, `(run_id, priority, score)`, weather lookup keys, and due jobs. A detection shared by overlapping regions must not be duplicated merely because another region requests it. Preserve asset lineage through a link table if deduplication connects multiple source assets.

### Internal service interfaces

```python
fetch_detections(region, start, end, product) -> DetectionBatch
import_archive(path, manifest) -> DetectionBatch
get_weather(cell, start, end, product) -> WeatherSeries
get_baseline(cell, month, baseline_version) -> ClimateBaseline
build_snapshots(region_id, as_of, detection_ids, config) -> list[ClusterSnapshot]
build_features(snapshot, as_of, weather, baseline) -> FeatureSet
score_features(feature_sets, scorer_version) -> list[ScoreResult]
create_evidence(score_result, feature_set, source_manifest) -> AlertEvidence
execute_run(region_id, mode, as_of, config) -> RunResult
```

Use Pydantic DTOs or dataclasses, not unstructured dictionaries between modules. A service performs its computation; repositories own database queries; the worker owns stage orchestration and run state.

## 6. Module 1 / Member 1: Backend foundation, database, and REST API

### Phase 1.1: Prepare accounts, tools, and repository

- [ ] Create the shared Git repository and grant access to five members.
- [x] Install Git, Python 3.12, and an editor. User selected native Windows development; Docker Desktop is not required. Native PostgreSQL 16 setup belongs to Phase 1.2.
- [x] Use Windows PowerShell locally with Python processes and native PostgreSQL 16. Linux containers remain a separate deployment option.
- [x] Create branches such as `module-1-api`, `module-2-firms`, `module-3-pipeline`, `module-4-ml`, `module-5-delivery`.
- [x] Establish short PRs, one reviewer, passing CI, and no direct shared-main changes.
- [x] Create `.gitignore`/`.dockerignore` excluding secrets, local environments, downloaded data, wallets, and private model bundles.
- [x] Commit `.env.example`, README, architecture decisions, issue checklist, and directory skeleton.

Module branches use the `codex/` prefix. Phase 1.1 tools are installed; collaborator invitations and independent PR review remain pending. Native PostgreSQL readiness belongs to Phase 1.2; Docker Desktop is not required.

Detailed completion evidence and pending account/tool gates: [Phase 1.1 checklist](docs/phase-1.1-checklist.md).

Suggested repository structure:

```text
firewatch-ai/
  pyproject.toml
  requirements.lock
  requirements-dev.lock
  .env.example
  .gitignore
  .dockerignore
  Dockerfile
  compose.yaml
  compose.prod.yaml
  alembic.ini
  migrations/
  src/firewatch/
    main.py
    cli.py
    config.py
    db/
    schemas/
    repositories/
    api/routes/
    sources/firms.py
    sources/open_meteo.py
    sources/power.py
    processing/clusters.py
    processing/features.py
    processing/baselines.py
    pipeline/runner.py
    pipeline/jobs.py
    worker.py
    ml/labels.py
    ml/train.py
    ml/evaluate.py
    ml/predict.py
    ml/explain.py
    evidence/canonical.py
    evidence/verify.py
    evidence/anchor.py
  contracts/
  configs/regions.yaml
  configs/features-v1.yaml
  configs/model-v1.yaml
  data/sample/
  models/README.md
  tests/unit/
  tests/integration/
  tests/contract/
  tests/e2e/
  scripts/
  deploy/Caddyfile
  docs/contracts.md
  docs/data-dictionary.md
  docs/model-card.md
  docs/runbook.md
  docs/frontend-handoff.md
  .github/workflows/ci.yml
```

### Phase 1.2: Bootstrap Python and local infrastructure

- [x] Define an installable `src/` package with runtime/dev dependency groups in `pyproject.toml`.
- [x] Generate exact compatible runtime and dev lock files, including transitive dependencies; verify on Python 3.12/Linux. Windows local verification and Linux hosted verification passed; see Phase 1.3 acceptance for the current development run.
- [x] Implement validated environment settings; missing required secrets fail startup clearly.
- [x] Set up native PostgreSQL 16 for Windows with a dedicated local role/database and persistent data directory; verify readiness with `pg_isready`. Compose configuration is a later Linux deployment deliverable.
- [x] Bind native local PostgreSQL to loopback only; document per-user process lifecycle, optional existing Windows service startup, and local backup paths. Never expose the database publicly in production.
- [x] Implement database sessions, connection pooling, and transaction boundaries.
- [x] Provide `firewatch --help` and `/health/live` as the first runnable interfaces.

Implementation and verification details: [Phase 1.2 acceptance record](docs/phase-1.2-checklist.md) and [native development guide](docs/local-development.md). Windows acceptance has passed; hosted Linux/Windows checks must pass before merging.

Local bootstrap commands after cloning the actual repository:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
# Native setup generates .env if absent. Preserve existing local credentials.
# For an external PostgreSQL install, configure .env manually instead.
.\.venv\Scripts\python.exe scripts/native_postgres.py setup
.\.venv\Scripts\firewatch.exe check-config
.\.venv\Scripts\firewatch.exe db-check
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\firewatch.exe seed-regions --config configs/regions.yaml
.\.venv\Scripts\python.exe -m uvicorn firewatch.main:app --reload
```

Use explicit `.venv` executable paths so Windows execution-policy settings do not block activation. The local database URL points to `localhost`; containers use the Compose service name `db`.

### Phase 1.3: Define schemas and migrations

- [x] Turn the shared table contract into SQLAlchemy models and versioned migrations.
- [ ] Agree DTOs with Members 2-5 before they write integration code.
- [x] Validate coordinates, allowed products, UTC timestamps, enum values, and numeric ranges.
- [x] Support nullable provider fields and preserved raw values where conversion fails.
- [x] Add uniqueness constraints, provenance links, and immutable alert restrictions.
- [x] Test migrations on an empty database and on the previous revision with sample data.
- [x] Seed one region through a repeatable CLI command, not a manual database edit.
- [x] Implement completed-run publication in a transaction: incomplete runs cannot become latest.

Implementation evidence: [Phase 1.3 acceptance](docs/phase-1.3-checklist.md),
[schema/publication rules](docs/database-schema.md), and [contracts-v1](docs/contracts.md).
DTO code and handoff documentation are ready; human agreement by Members 2-5 is
an external review step and remains unchecked.

### Phase 1.4: Build read APIs

All routes use `/api/v1` except health routes. Implement typed responses and stable operation IDs.

| Method/path | Purpose and important behavior |
|---|---|
| `GET /health/live` | Process liveness; no dependency calls |
| `GET /health/ready` | Database and required local artifacts ready; return 503 if unavailable |
| `GET /api/v1/regions` | Region names, bounding boxes, available modes |
| `GET /api/v1/status` | Source freshness, last successful refresh, scorer availability; sanitize details |
| `GET /api/v1/runs` | Filter by region/mode; completed/failed run history |
| `GET /api/v1/runs/{run_id}` | Run configuration summary, stages, completeness |
| `GET /api/v1/hotspots` | Required run_id; paginated ranking and optional priority/bbox filters |
| `GET /api/v1/hotspots/{snapshot_id}` | Full snapshot, evidence, features, timestamps, score and explanation |
| `GET /api/v1/hotspots.geojson` | Required run_id; bounded FeatureCollection using lon/lat |
| `GET /api/v1/summary` | Required run_id; counts using the same filters as hotspot list |
| `GET /api/v1/models/current` | Active model/baseline metadata and limitations |
| `GET /api/v1/replays` | Prepared historical scenarios and immutable completed run IDs |
| `GET /api/v1/alerts/{alert_id}` | Saved evidence and integrity/anchoring status |
| `GET /api/v1/alerts/{alert_id}/verify` | Read-only hash/anchor verification; no new transaction |
| `GET /api/v1/jobs/{job_id}` | Operator-authenticated progress and sanitized failure detail |

- [x] Default page size 50, maximum 200; cap GeoJSON features at 2,000 with explicit truncation metadata.
- [x] Sort by descending priority score, then snapshot UUID for stable pagination.
- [x] Return `total`, `limit`, `offset`, `run_id`, `mode`, `as_of`, and freshness metadata.
- [x] Return empty collections for a valid completed run with no detections.
- [x] Use 404 for unknown IDs, 422 for invalid filters, and 503 for required unavailable dependencies.
- [x] Do not fall back silently from requested replay to live data.
- [x] Export `/openapi.json` as a CI artifact for frontend integration.

Implementation and native PostgreSQL acceptance are documented in
`docs/phase-1.4-checklist.md` and `docs/frontend-handoff.md`.

### Phase 1.5: Operator endpoints and security

| Method/path | Operator action |
|---|---|
| `POST /api/v1/jobs/refresh` | Queue bounded regional refresh |
| `POST /api/v1/jobs/replay` | Queue an allowlisted historical scenario |
| `POST /api/v1/alerts/{alert_id}/anchor` | Queue an optional anchor transaction |

- [x] Require a server-configured high-entropy bearer token for every mutation and job-detail response.
- [x] Do not ship an operator token in future browser frontend code. Operator actions stay server-side or require a later real login system.
- [x] Permit public reads only after deciding whether the deployment is public; document that policy.
- [x] Require `Idempotency-Key` for mutation requests; identical retries return the original job, conflicting bodies return 409.
- [x] Restrict regions, time windows, replay IDs, request size, and job queue depth.
- [x] Keep training, model promotion, and archive imports as trusted CLI operations.
- [x] Configure CORS with explicit future frontend origins; no wildcard credentialed origins.
- [x] Redact authorization headers, FIRMS key-bearing URL paths, database credentials, and private keys from logs.

Implementation, operator contracts and acceptance are documented in
`docs/operator-api.md` and `docs/phase-1.5-checklist.md`.

### Phase 1.6: Acceptance and handoff

- [x] A clean database migrates and seeds successfully.
- [x] Swagger `/docs` can demonstrate all read operations with sample data.
- [x] Protected operations reject missing/wrong tokens and enforce idempotency.
- [x] API never returns cross-run/live-replay mixed results.
- [x] API contracts, error format, and field dictionary are committed.
- [x] Contract tests cover timestamps, units, pagination, GeoJSON order, and null probability behavior.

Deliverable: running API skeleton, database migrations, validated DTOs, OpenAPI specification, and API test suite.

Acceptance evidence and reproducible commands: `docs/phase-1.6-checklist.md`.
The Swagger walkthrough and frontend bundle use explicitly synthetic isolated fixtures;
real ingestion, scoring, replay execution and chain integration remain later modules.

## 7. Module 2 / Member 2: Satellite ingestion and historical data

### Phase 2.1: Prove source access and choose the first region

- [x] Use a valid FIRMS MAP_KEY stored only in the ignored local `.env`; the credential is never recorded in source or evidence.
- [x] Request full seasonal archive access through the authenticated FIRMS portal: NOAA-20 CSV orders `828115` (2022) and `828116` (2024) are submitted, with delivery pending.
- [x] Query actual NOAA-20/NOAA-21 NRT/SP date availability and record product intervals.
- [x] Select the bounded northern California candidate from two official CAL FIRE incident records and complete ten historical-weather point probes.
- [x] Record bbox, May–October study season, requested 2022/2024 months, actual full-month product availability and product policy in `configs/regions.yaml`.
- [x] Download and inspect one recent NOAA-20 NRT CSV (valid header-only response) and two three-day NOAA-20 SP historical samples; save sanitized metadata/checksums.
- [x] Keep NOAA-20 consistent for the primary cohort; keep NOAA-21 as comparison-only until separate comparisons and human review.

See [Phase 2.1 acceptance](docs/phase-2.1-checklist.md), [source-access study](docs/source-access.md), and [sanitized provider evidence](docs/source-access-firms.json). The candidate stays disabled. Both seasonal orders were observed as `Submitted`; [archive request evidence](docs/source-access-archive-requests.json) records the exact parameters. Both seasonal archives were subsequently delivered and imported in Phase 2.4; independent human source review remains outstanding.

### Phase 2.2: Implement the FIRMS client

- [x] Build requests only from allowlisted hosts/products and validated bbox/date inputs.
- [x] Implement connect/read timeouts, bounded retries with exponential backoff/jitter, and rate-limit handling.
- [x] Respect provider limits and `Retry-After`; count requests/transactions conservatively.
- [x] Detect HTML/provider error text even when the HTTP response is 200.
- [x] Distinguish an empty valid CSV from a malformed response or provider outage.
- [x] Save raw response bytes and a sanitized request manifest before normalization.
- [x] Never log the full FIRMS URL because the credential is in the URL path.
- [x] Return a typed batch plus retrieval and product metadata.

Implementation: [FIRMS client guide](docs/firms-client.md), [Phase 2.2 acceptance](docs/phase-2.2-checklist.md), and [real bounded fetch evidence](docs/phase-2.2-smoke.json). `fetch-firms` returns typed raw rows and source metadata; normalization, deduplication and database ingestion remain Phases 2.3–2.5. Quota reservations are conservative estimates supplemented by provider status, not an exact provider charging formula.

### Phase 2.3: Normalize and deduplicate

- [x] Parse `acq_date` + zero-padded `acq_time` as UTC, including values like `0037`.
- [x] Validate lat/lon; reject impossible coordinates and negative/invalid FRP as configured.
- [x] Preserve raw confidence values; map valid classes consistently without fabricating percentages.
- [x] Preserve sensor/product/processing status and optional brightness, scan, track, day/night.
- [x] Define a versioned deterministic observation key from product, satellite, acquisition time, source coordinate precision, and scan/track where provided.
- [x] Do not merge NOAA-20 and NOAA-21 observations simply because coordinates match.
- [x] For corrected NRT/science records, preserve revision lineage and decide which product is selected for each run. Do not count revisions as separate activity.
- [x] Persist rejected rows and reasons in a small quality report.
- [x] Upsert using database uniqueness constraints, not only in-memory deduplication.

Implementation: [normalization and revision-selection guide](docs/firms-normalization.md), [Phase 2.3 acceptance](docs/phase-2.3-checklist.md), and [aggregate real-source verification](docs/phase-2.3-smoke.json). The existing source lineage and run manifest schema carry versioned corrections and frozen selection; archive import and scheduling remain the following phases.

### Phase 2.4: Build historical import

- [x] Request several months/seasons of archive data if available; keep an untouched original copy.
- [x] Implement `firewatch import-firms --path ... --manifest ...` for trusted local CSV input.
- [x] The manifest records source URL, product, sensor, date interval, region, checksum, retrieval time, and license/attribution reference.
- [x] Reuse the exact live normalizer; chunk large files and commit in bounded batches.
- [x] Detect missing date intervals, mixed products, duplicate revisions, and unexpectedly empty periods.
- [x] Import at least 24 hours before the earliest feature cutoff and 24 hours after the last label cutoff; add weather/baseline history separately.
- [x] Create a data inventory by month, sensor, class, region, and number of distinct fire periods.
- [x] Hand Member 4 a real-data manifest; never substitute invented labels for missing history.

Implementation: [historical import guide](docs/firms-archive-import.md), [Phase 2.4 acceptance](docs/phase-2.4-checklist.md), [real monthly inventory](docs/historical-data-inventory.json), and [seasonal delivery evidence](docs/source-access-archive-deliveries.json). Both full seasonal CSVs are imported: 17,862 distinct NOAA-20 SP observations across 372 calendar days, with untouched originals and zero repeat insertions. Training labels, event review and weather/baseline history remain their later phases.

### Phase 2.5: Make refresh idempotent and observable

- [x] Ingest an overlapping 48-hour window at each refresh to catch delayed records and updates.
- [x] Chunk requests inside provider limits and keep progress per chunk.
- [x] Maintain separate batch states for each sensor/product.
- [x] Report fetched/accepted/rejected/duplicate counts, latest acquisition, and last successful retrieval.
- [x] A failed sensor request marks partial source coverage; it does not imply zero fires.
- [x] Publish source status to Member 1 and normalized records to Member 3.

Implementation: [48-hour source refresh guide](docs/firms-refresh.md), [Phase 2.5 acceptance](docs/phase-2.5-checklist.md), and [real source/status evidence](docs/phase-2.5-smoke.json). Durable per-chunk progress, independent product coverage and the normalized-record handoff are callable from the later job worker. The source stage does not complete a pipeline Run or activate the study region.

### Phase 2.6: Acceptance and handoff

- [x] Reimporting the same file creates no duplicate observations.
- [x] Leading-zero times, no-fire responses, malformed CSV, timeout, rate limit, and corrected records are tested.
- [x] Live and archive data use identical normalized schemas and unit conventions.
- [x] Historical inventory includes enough distinct periods to attempt event-separated evaluation; otherwise flag the dataset limitation early.
- [x] No source secrets appear in logs, fixtures, manifests, or exceptions.

Deliverable: FIRMS adapter, archive importer, normalized data, provenance manifests, and ingestion-quality report.

Acceptance: [Phase 2.6 evidence](docs/phase-2.6-checklist.md) and [verified source handoff](docs/firms-handoff.md). Both real seasonal reimports inserted zero observations. The historical event/label limitation is explicitly flagged; ingestion acceptance does not claim evaluation or training readiness.

## 8. Module 3 / Member 3: Weather, climate, clustering, jobs, and replay

### Phase 3.1: Weather clients and caching

- [x] Implement separate live, historical-forecast, and optional reanalysis adapters behind one weather DTO.
- [x] Request explicit UTC timestamps and m/s wind; validate returned unit metadata.
- [x] Fetch historical weather for historical hotspot dates. Never use live weather during replay.
- [x] Cache at documented cell centers, initially a 0.1-degree grid; store requested and returned coordinates and grid version.
- [x] Cache hourly series by provider/product/model, cell, time interval, and source version.
- [x] Batch cell requests when supported; use conservative concurrency and shared request budgets.
- [x] Select the last completed weather interval ending at or before T; define precipitation interval semantics from provider metadata.
- [x] Build rolling 24-hour precipitation only when all required hourly intervals exist.
- [x] Track stale, missing, fallback-product, and interpolated data explicitly.

Historical Forecast is preferred to reduce product differences from live model weather. Its stitched archive does not alone prove what was available at an exact historical issue time. Exact issue-time backtesting requires individual run/availability metadata and is outside the first retrospective MVP. [S5]

Implementation: [weather clients and cache guide](docs/weather-cache.md),
[Phase 3.1 acceptance](docs/phase-3.1-checklist.md), and
[real historical cache evidence](docs/phase-3.1-smoke.json). Separate live,
historical-forecast and explicit ERA5 reanalysis adapters share the weather DTO.
Immutable PostgreSQL revisions, bounded batched requests, shared request budgets,
past-only selection and complete-hour rainfall checks are implemented. Native
provider interpolation is marked unknown; no local interpolation is performed.
Climate context, clustering, features, worker execution and replay orchestration
remain the following phases.

### Phase 3.2: Precompute climate context

- [ ] Use NASA POWER T2M and PRECTOTCORR first; add parameters only after checking availability.
- [ ] Choose a fixed reference period ending before the earliest evaluation/training snapshot, preferably at least 10 complete years if available.
- [ ] Fetch by unique weather/climate cells and bounded year intervals, not once per hotspot.
- [ ] Read provider fill values and units; convert fill sentinels to null before statistics.
- [ ] Compute monthly reference mean daily temperature and mean daily precipitation, with sample counts.
- [ ] Compute `temperature_departure_c = temperature_at_T - monthly_mean_daily_temperature`.
- [ ] Label that value as a departure from a daily climate reference, not a standardized hourly heat anomaly.
- [ ] Compute `rainfall_deficit_24h_mm = max(0, monthly_mean_daily_precip_mm - observed_prior_24h_precip_mm)` only with a complete preceding-day weather series.
- [ ] Record reference period, UTC daily convention, coarse grid resolution, statistic definitions, and baseline version.
- [ ] Do not use a daily weather value covering hours after T as an input at T.
- [ ] If a baseline is missing, record the missing flag; do not invent a climate value.

### Phase 3.3: Build reproducible cluster snapshots

- [ ] Define an analysis cutoff T in UTC; candidate detections come from `(T-6h, T]`.
- [ ] Exclude future observations even if they are already present in the database for training/replay.
- [ ] Initially use spatial DBSCAN: haversine, `eps = 2 / 6371.0088` radians, `min_samples=1`.
- [ ] Restrict inputs to that six-hour window; this provides the initial temporal boundary.
- [ ] Sort inputs deterministically; derive snapshot IDs from run ID and sorted member IDs or persist once per run.
- [ ] Calculate centroid, FRP max/sum, latest observation, sensor distribution, and confidence counts.
- [ ] For each centroid, calculate prior detections within 2 km for `(T-6h,T]`, `(T-12h,T]`, `(T-24h,T]`.
- [ ] Keep clusters run-specific. Do not claim the same DBSCAN cluster ID tracks a physical fire across dates.
- [ ] Document that nearby unrelated anomalies can merge; inspect representative cases and version any radius changes.
- [ ] Use fixed spatial/time definitions in training and live processing.

### Phase 3.4: Implement feature schema v1

| Feature | Definition |
|---|---|
| `frp_max_mw`, `frp_sum_mw` | Aggregates of current snapshot member detections |
| `brightness_max_k` | Valid maximum reported I-4 brightness, nullable |
| `detections_6h/12h/24h` | Past-only observations in the configured centroid radius |
| `hours_since_first_seen_24h` | Age within the 24-hour lookup, not claimed full incident age |
| `confidence_high_fraction` | Fraction of valid snapshot detections with normalized high confidence |
| `day_fraction` | Fraction of current members marked daytime |
| `temperature_c`, `humidity_pct` | Last valid preceding weather interval |
| `wind_ms`, `gust_ms` | Same timestamp/product semantics as training |
| `precipitation_24h_mm` | Sum of complete preceding hourly intervals |
| `temperature_departure_c` | Difference from fixed monthly climate reference |
| `rainfall_deficit_24h_mm` | Reference daily rainfall minus preceding-day rainfall, floored at zero |
| `weather_age_hours` | T minus weather valid time |
| Missing indicators | One per nullable model input where appropriate |

- [ ] Keep IDs, future labels, risk labels, future sensor counts, and anchored status out of model inputs.
- [ ] Exclude absolute coordinates initially to reduce location memorization; retain them for maps and grouping.
- [ ] Store feature values, unit map, source asset IDs, feature hash, feature version, and cutoff.
- [ ] Use one shared feature builder for training, replay, and live scoring.
- [ ] Add boundary tests proving later database records cannot change a previously saved snapshot.

### Phase 3.5: Implement durable jobs and run orchestration

- [ ] Add a dedicated `python -m firewatch.worker` process; do not run the scheduler inside Uvicorn workers.
- [ ] Scheduler creates one refresh job per configured region/15-minute slot using a unique slot key.
- [ ] Worker claims due jobs transactionally with row locking; use a lease and heartbeat for crash recovery.
- [ ] Persist `queued`, `running`, `succeeded`, `failed`, and retry state with bounded attempts.
- [ ] Pipeline stages: ingest -> snapshot -> weather/baseline -> features -> score -> evidence/outbox -> publish.
- [ ] Use short transactions per stage, idempotent writes, and recorded checkpoints.
- [ ] Do not hold database locks during provider network calls or ML inference.
- [ ] A crashed job can resume safely; an expired lease can be reclaimed without duplicate alerts.
- [ ] Prevent overlapping jobs for the same region/mode/cutoff using a unique run key or advisory lock.
- [ ] Publish a run only after required stages succeed; store partial-coverage warnings separately.
- [ ] Prefer `weather age > 3h` as an initial stale flag and detections older than 24h as stale source status; these are configurable prototype policies, not provider guarantees.

### Phase 3.6: Create deterministic replay

- [ ] Prepare at least three real-data scenarios: varied activity, quieter/empty period, and degraded-source example.
- [ ] Bundle their raw/canonical data manifest and required cached weather/baselines.
- [ ] Implement `firewatch replay --scenario <id>` using an injected clock and fixed as_of.
- [ ] Replay performs no live weather calls and never writes to the latest-live pointer.
- [ ] Mark all outputs `mode=replay`; expose detection time and actual later processing time separately.
- [ ] Repeating the same scenario/model/config produces identical feature hashes and numeric results within documented numerical tolerance.
- [ ] Keep synthetic failure fixtures clearly labeled and separate from real replay evidence.

### Phase 3.7: Acceptance and handoff

- [ ] Verify joins, time boundaries, distance calculation, missing values, units, and empty runs.
- [ ] Test rainfall interval completeness and provider fill values.
- [ ] Simulate process termination mid-run and demonstrate safe retry/recovery.
- [ ] Run a cached replay with network access disabled.
- [ ] Hand Member 4 Parquet feature snapshots plus schema/manifests; hand Member 1 run/status data.

Deliverable: time-matched feature pipeline, cache, climate baselines, durable worker, and offline replay scenarios.

## 9. Module 4 / Member 4: Labels, ML, evaluation, scoring, explanations

### Phase 4.1: Freeze target and dataset design

- [ ] Write the target specification before training: “Will a later detection within 2 km of the current snapshot centroid show stronger FRP during `(T,T+24h]`?”
- [ ] Initial positive: a valid later detection has FRP greater than `current_frp_max + max(5 MW, 0.20 * current_frp_max)`.
- [ ] Initial negative: no qualifying stronger detection is observed in the complete follow-up archive window.
- [ ] Mark negatives with no later detections as `unobserved_followup=true`. They mean “not observed to strengthen,” not “confirmed extinguished.”
- [ ] Exclude incomplete end-of-dataset windows, known provider outages, and invalid FRP baselines as unknown labels.
- [ ] A full downloaded archive window is not proof of cloud-free satellite coverage. State remaining observation bias and evaluate sensitivity separately for negatives with/without later detections.
- [ ] Use a six-hour snapshot cadence for training initially; avoid thousands of near-identical minute-by-minute rows.
- [ ] Treat 2 km, 24 hours, 5 MW, and 20% as versioned prototype settings. Change them using development/validation only, never the final test set.
- [ ] Evaluate sensor/product differences and overpass effects. The target is a satellite-observation proxy, not physical severity ground truth.

### Phase 4.2: Create leakage-resistant splits

- [ ] Assign whole fire periods to event groups using documented spatial/temporal matching; inspect merges/splits.
- [ ] Keep event grouping for partitioning separate from past-only feature construction; event IDs are never model features.
- [ ] Create chronological train, validation, calibration/threshold selection, and final-test blocks when data volume permits.
- [ ] Purge at least 48 hours around partition boundaries to separate 24-hour lookback and 24-hour label windows.
- [ ] Remove or assign cross-boundary event groups so the same event cannot appear in multiple partitions.
- [ ] Fit imputers, encoders, scaling, and model hyperparameters using training/development partitions only.
- [ ] Freeze baseline reference dates and feature definitions before evaluating the final holdout.
- [ ] Save split membership, excluded rows/reasons, row/event counts, class balance, and dataset checksum.
- [ ] If there are too few independent events/classes, use grouped temporal cross-validation within development data and retain a final holdout where possible. Otherwise release an honest baseline-only MVP.

### Phase 4.3: Implement baselines first

- [ ] Baseline A: rank clusters by FRP maximum only.
- [ ] Baseline B: implement a documented weighted index using intensity, humidity, wind, and previous detections; freeze transformations/weights using development data.
- [ ] Call rule output `priority_score`, never `escalation_probability`.
- [ ] Train a logistic-regression pipeline as a simple supervised comparison if sample size allows.
- [ ] Evaluate all methods on identical held-out snapshots and labels.
- [ ] Make baseline scoring runnable before ML artifact availability so the API/pipeline can integrate immediately.

### Phase 4.4: Train Random Forest and calibrate

- [ ] Build a scikit-learn Pipeline including deterministic preprocessing and missing indicators.
- [ ] Start with a small bounded search over tree count, maximum depth, and minimum leaf size; set random seeds.
- [ ] Choose class weighting from development data and compare effects on ranking/calibration.
- [ ] Use validation results to choose model settings, not the final test set.
- [ ] If supported by sufficient held-out samples/classes, fit sigmoid calibration on a disjoint calibration partition; verify calibration performance. [S9]
- [ ] Keep a separate validation portion or cross-fitted development procedure for threshold selection when possible; document any small-data compromise.
- [ ] Save estimator, preprocessing, calibrator, ordered feature names, dependency versions, configuration, and checksums.
- [ ] If calibration evidence is insufficient, expose the output as `model_score`; leave `escalation_probability=null`.
- [ ] Do not train in the deployed API or load untrusted joblib/pickle files.

### Phase 4.5: Evaluate and choose priority thresholds

- [ ] Report precision, recall, F1, PR-AUC, class prevalence, and confusion matrix.
- [ ] Report precision@10 and recall@10 for per-run ranking; use `k=min(10,n)` and exclude runs without positives from recall aggregation with counts reported.
- [ ] Report Brier score/reliability plots for any claimed probability output.
- [ ] Break results down by event, month, sensor/product, weather completeness, and unobserved-followup flag.
- [ ] Use event-level resampling for uncertainty estimates when feasible; do not treat repeated snapshots as independent fires.
- [ ] Compare ML and both baselines; document whether weather/climate features improve results through an ablation comparison.
- [ ] Choose Low/Medium/High/Critical thresholds on development data; store them in scorer metadata.
- [ ] The original 0.35/0.55/0.75 boundaries may be an initial display convention, never a validated claim by default.
- [ ] If using baseline scores, thresholds belong to the baseline scale and must be explicitly labeled provisional.
- [ ] Open the final test once for release evaluation; save a model card with failures and generalization limits.
- [ ] Promote ML only if held-out ranking quality and limitations justify it. Never invent a target accuracy or achieved metric.

### Phase 4.6: Implement inference and explanation contract

- [ ] Implement `score_features()` as a batch interface; load the approved trusted artifact once per process.
- [ ] Validate feature schema/version/order and finite numeric values before inference.
- [ ] Return `scorer_version`, `score_kind`, `priority_score`, nullable `escalation_probability`, `priority`, and warnings.
- [ ] Return actual SHAP contributions for the underlying tree output, with contribution units clearly stated.
- [ ] If probability calibration follows the tree, explain the base model's ranking drivers; do not claim SHAP values sum to the calibrated probability.
- [ ] Produce top-three text reasons from deterministic templates tied to valid feature contributions.
- [ ] Handle negative contributions and missing/imputed features honestly; never label a missing weather value as observed dry/windy conditions.
- [ ] Baseline explanations must expose actual weighted terms; no fabricated SHAP values.
- [ ] No LLM is needed for numeric scoring or explanatory text in this build.

### Phase 4.7: Registry, promotion, and safe failure

- [ ] Implement `firewatch train`, `evaluate`, and `promote-model --version ...` CLI commands.
- [ ] Training is offline; promotion checks artifact digest, schema compatibility, metrics, and model card.
- [ ] Record approved model version per run. Never rescore old saved alerts in place.
- [ ] On model failure, either fail the run clearly or run an explicitly configured baseline with `score_kind=baseline`; never silently swap methods.
- [ ] Missing optional values may use training-fitted imputation with warnings; widespread missing required inputs should withhold ML scoring under documented quality rules.
- [ ] Keep the previous approved model available for rollback.

### Phase 4.8: Acceptance and handoff

- [ ] Labels have test cases for exact time/radius/FRP boundaries and incomplete follow-up.
- [ ] Future records cannot affect features at T.
- [ ] Split tests prove event disjointness and purge intervals.
- [ ] Serialized and in-memory inference agree on fixtures.
- [ ] API returns null probability for baseline/unvalidated-score cases.
- [ ] Supply model card, training manifest, evaluation report, approved bundle, and reproducible CLI commands.

Deliverable: honest, reproducible prioritization model or documented baseline release, plus explanations and evaluation evidence.

## 10. Module 5 / Member 5: Evidence, optional blockchain, QA, and deployment

### Phase 5.1: Canonical evidence payload and hashing

- [ ] Define `evidence-v1` including alert/run/snapshot IDs, mode, region, cutoff, satellite input IDs/hash manifest, weather values/times/product, baseline version, full feature vector/hash, scorer/model/feature versions, score/priority, explanation, warnings, source checksums, and scoring time.
- [ ] Use a fixed representation: UTF-8, sorted JSON keys, compact separators, UTC timestamps, explicitly specified decimal strings, null rules, and no NaN/Infinity.
- [ ] Sort unordered collections before serialization. Specify field precision before creating either the saved or anchored digest.
- [ ] Generate canonical bytes once and store them immutably alongside SHA-256 hex digest.
- [ ] Preserve exact canonical bytes/text, even if a JSONB copy exists for querying; do not rely on database JSON formatting for hashing.
- [ ] Include evidence schema/canonicalization version. Future format changes create new versions.
- [ ] Make evidence generation independent of chain availability.
- [ ] Create evidence and its anchor outbox entry transactionally after scoring.

### Phase 5.2: Verification API and audit behavior

- [ ] Recompute the saved payload hash and compare with the original stored digest.
- [ ] If anchored, additionally compare with the digest retrieved from the recorded chain/contract.
- [ ] Expose distinct states: `local_match`, `local_mismatch`, `unanchored`, `anchor_pending`, `anchor_match`, `anchor_mismatch`, `anchor_unavailable`.
- [ ] Return chain status without converting a network outage into a mismatch.
- [ ] Confirm referenced artifact checksums if those assets are available; distinguish payload verification from full artifact verification.
- [ ] Explain that a local hash can detect accidental changes, but an attacker controlling both payload and stored hash can rewrite both. An independently anchored digest supplies a separate comparison point.
- [ ] State that integrity verification does not prove a fire is real or a model is correct.
- [ ] Demonstrate tampering using a copied test payload, never by modifying an immutable real alert.

### Phase 5.3: Optional Solidity contract

- [ ] Complete local integrity first; confirm the actual hackathon blockchain requirement separately before submission.
- [ ] Use a minimal `AlertRegistry`: authorized writer, `bytes32 alertKey`, `bytes32 evidenceDigest`, and an anchoring event.
- [ ] Use the backend SHA-256 payload digest directly as `bytes32`; do not accidentally replace it with keccak over differently encoded content.
- [ ] Keep model version in the hashed payload/event metadata; do not put coordinates/full weather data on-chain.
- [ ] Reject unauthorized writes and conflicting overwrites; define same-digest repeat behavior.
- [ ] Test authorized/unauthorized calls, duplicate IDs, stored digest reads, and events using Forge.
- [ ] Pin a released Solidity compiler/toolchain version; do not use a development compiler build.
- [ ] Start Anvil locally and deploy with a dedicated test key; never reuse its public development keys for valuable assets.
- [ ] Bind local chain RPC to loopback/internal network only.

### Phase 5.4: Reliable anchoring worker

- [ ] Persist one outbox record per alert/network and process it separately from scoring.
- [ ] Keep one signer process initially to avoid transaction nonce conflicts.
- [ ] Store transaction hash before waiting for a receipt; retries first check chain/contract state.
- [ ] Track queued/submitted/confirmed/failed status, receipt result, chain ID, contract address, and block hash/height.
- [ ] Distinguish reverted, pending, RPC-unavailable, and missing-funds conditions.
- [ ] If using a public testnet, choose one supported at implementation time, obtain faucet funds, and document confirmations/reorg handling.
- [ ] Local-chain records are a demonstration and may reset; publish `network_kind=local` and chain identity clearly.
- [ ] Do not label local anchoring as publicly independent notarization.
- [ ] Never let an unavailable chain prevent analyst results from being published.

### Phase 5.5: CI and integration quality

- [ ] GitHub Actions installs locked dependencies and runs Ruff, mypy, pytest, and a container build.
- [ ] Use a PostgreSQL service matching the deployment major version for migration/integration tests.
- [ ] Mock external APIs with recorded sanitized fixtures; CI must not require live keys or network provider availability.
- [ ] Test: import -> cluster -> enrich -> feature -> baseline/model -> alert -> API -> verify.
- [ ] Separately allow a manual opt-in live provider smoke test with secrets.
- [ ] Keep a trusted, small test model artifact or build one deterministically inside tests; never download arbitrary pickle files.
- [ ] Run contract tests for canonical evidence bytes/hash and OpenAPI response compatibility.
- [ ] Test worker crash recovery, duplicate job submission, partial provider outage, empty region, missing model, and replay/live isolation.
- [ ] Export test reports, OpenAPI, and build metadata as CI artifacts.
- [ ] Add dependency vulnerability scanning and secret scanning to the release workflow.

### Phase 5.6: Build containers and local full-stack backend

- [ ] Build one Linux Python image from the locked package set; run as a non-root user.
- [ ] API command: `uvicorn firewatch.main:app --host 0.0.0.0 --port 8000`.
- [ ] Worker command: `python -m firewatch.worker`.
- [ ] Migration command: `alembic upgrade head`, executed once as a release step.
- [ ] Define Compose services `db`, `api`, `worker`; optional `anvil`; production adds `caddy`.
- [ ] Use DB health checks/startup conditions plus application retry logic, rather than assuming container start means database readiness. [S8]
- [ ] Mount persistent PostgreSQL, raw-data, model, and Caddy storage volumes.
- [ ] API mounts model artifacts read-only; worker/data paths receive only needed write permissions.
- [ ] Add readiness checks and restart policies; preserve logs on stdout/stderr.
- [ ] Keep real training/history archives outside the image; copy approved model artifacts through a versioned deployment bundle.
- [ ] Verify full backend starts locally from the README with no frontend process.

### Phase 5.7: Deploy on a Linux server

Deployment target: one Linux VM with Docker Compose, persistent disk, and a public API hostname. Budget an initial 2 vCPU / 4 GB RAM for serving and small regional processing, then measure usage. Train larger models offline. Hosting/domain costs are separate from free source-data access.

1. [ ] Provision the server and configure SSH key access for the release owner.
2. [ ] Install Docker/Compose from official packages; create the application directory and persistent storage paths.
3. [ ] Configure DNS for the API hostname. Permit public 80/443; restrict SSH; do not publish database/chain RPC ports.
4. [ ] Pull a reviewed Git release or immutable container image tag. Prefer a release SHA/image digest over `latest`.
5. [ ] Create a server-only `.env`/secret file with restrictive permissions; use separate deployment credentials.
6. [ ] Define production Compose overrides: no source bind mounts, no reload, internal service network, appropriate memory limits.
7. [ ] Configure Caddy to proxy the API hostname to `api:8000`, persist certificate state, and verify DNS/ACME prerequisites. [S10]
8. [ ] Start PostgreSQL and wait for its health check.
9. [ ] Run migrations once, seed the chosen region, and install the approved model bundle or explicitly select baseline mode.
10. [ ] Copy/import a small prepared replay bundle with matching source and artifact checksums.
11. [ ] Start API, worker, and proxy; optional chain stays internal.
12. [ ] Verify HTTPS, readiness, source status, protected actions, a completed replay, and one live refresh.
13. [ ] Restart containers/server and confirm data, jobs, models, and alerts survive.
14. [ ] Record release SHA, model version, feature version, migration revision, and deployment timestamp.

Release command contract, run on the server after files/secrets exist:

```bash
docker compose -f compose.yaml -f compose.prod.yaml config --quiet
docker compose -f compose.yaml -f compose.prod.yaml up -d db
docker compose -f compose.yaml -f compose.prod.yaml run --rm api alembic upgrade head
docker compose -f compose.yaml -f compose.prod.yaml run --rm api firewatch seed-regions --config configs/regions.yaml
docker compose -f compose.yaml -f compose.prod.yaml up -d api worker caddy
docker compose -f compose.yaml -f compose.prod.yaml ps
```

The image entry point must allow these commands. Never print resolved Compose configuration containing secrets into CI logs. Docker Compose is the chosen initial deployment strategy, not a guarantee of high availability. [S7]

### Phase 5.8: Backups, monitoring, rollback, and handoff

- [ ] Back up PostgreSQL daily and before migrations using an encrypted/off-server destination.
- [ ] Back up immutable raw assets, replay bundles, approved model artifacts, manifests, and optional local-chain state separately.
- [ ] Perform a restore test into a fresh database and verify a saved alert and replay.
- [ ] Monitor API error rate/latency, job queue age, worker heartbeat, last successful ingestion, disk usage, provider errors, and model availability.
- [ ] Keep health probes lightweight; readiness should not call external providers on every request.
- [ ] Define an incident runbook for provider outage, worker crash, exhausted disk, DB failure, model failure, and chain outage.
- [ ] Roll back application/model versions only if compatible with the current schema; use forward fixes or a verified backup restore for incompatible migrations.
- [ ] Disable duplicate schedulers during deployment; verify only one active scheduling leader.
- [ ] Give the future frontend team API base URL, OpenAPI, sample responses, enums, timestamps, pagination, CORS policy, and limitations.

Deliverable: integrity layer, optional chain adapter, CI pipeline, deployment configuration, verified running backend, and operations runbook.

## 11. Environment configuration checklist

Implement these through validated settings; `.env.example` contains placeholders only.

```dotenv
APP_ENV=development
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://firewatch:REPLACE_ME@localhost:5432/firewatch
POSTGRES_DB=firewatch
POSTGRES_USER=firewatch
POSTGRES_PASSWORD=REPLACE_ME
FIRMS_MAP_KEY=REPLACE_ME
ADMIN_API_TOKEN=REPLACE_WITH_RANDOM_SECRET
CORS_ORIGINS=[]
REGIONS_CONFIG=configs/regions.yaml
RAW_DATA_DIR=data/raw
MODEL_DIR=models
ACTIVE_SCORER=baseline-v1
REFRESH_INTERVAL_MINUTES=15
WEATHER_CACHE_GRID_DEGREES=0.1
WEATHER_MAX_AGE_HOURS=3
CLUSTER_RADIUS_KM=2
SNAPSHOT_LOOKBACK_HOURS=6
LABEL_HORIZON_HOURS=24
FEATURE_SCHEMA_VERSION=features-v1
EVIDENCE_SCHEMA_VERSION=evidence-v1
CHAIN_ENABLED=false
CHAIN_RPC_URL=http://127.0.0.1:8545
CHAIN_ID=31337
ALERT_REGISTRY_ADDRESS=
CHAIN_PRIVATE_KEY=
PUBLIC_API_HOSTNAME=
```

Container configuration overrides DB/RPC hosts to service names and paths to mounted container paths. Do not check `.env` into Git. Production uses different credentials. Chain secrets are absent when chain integration is disabled.

## 12. Integration sequence and team working order

These are coordination waves, not five sequential member assignments. Members work concurrently against agreed contracts once foundations exist.

| Wave | Member 1 | Member 2 | Member 3 | Member 4 | Member 5 | Exit gate |
|---|---|---|---|---|---|---|
| A: contracts/setup | Repo, DTOs, migrations | Source samples/access | Weather proof, feature spec | Target/split specification | CI scaffold, evidence spec | Shared contracts agreed |
| B: first vertical slice | Read APIs | Small real detection batch | Snapshots + cached weather | Baseline scorer | Hash verification | One real run visible through API |
| C: historical pipeline | Jobs/status APIs | Archive inventory/import | Features/replay/worker | Labels + training | Integration tests, containers | Offline replay works |
| D: evaluated release | Auth/error contracts | Source resilience | Retry/recovery/completeness | Held-out metrics/explanations | Optional chain, deploy staging | Release acceptance passes |
| E: delivery | OpenAPI handoff | Source credits | Operational runbook input | Approved model card | HTTPS deployment/restore test | Backend deployed and documented |

### Critical dependency rules

- Module 4 needs real historical inputs from Modules 2/3, but can build label and scorer contracts early using clearly synthetic test fixtures.
- Module 1 can implement endpoint schemas against fixtures before production data is ready.
- Module 5 can implement evidence canonicalization and CI before the final model exists.
- A schema/feature change requires a version bump and communication before merge.
- Merge a working real-data baseline path before improving model complexity or adding blockchain.

### Planning estimate

Assume five members, one region, basic Python/API skills, and several working days rather than a single evening. Use 7-10 focused working days as an initial planning budget, subject to archive access and dataset quality; it is not a delivery guarantee. For a short hackathon, cut optional blockchain automation, advanced calibration, and extra regions first. Preserve real data, replay, honest scoring, and the core API.

### End-to-end command contract

Implement these CLI names/options consistently in `firewatch.cli`. They are the expected operator workflow after the corresponding phases are built, not commands that already exist in this document's directory. Paths below are repository-relative.

| Command | Required result |
|---|---|
| `firewatch seed-regions --config configs/regions.yaml` | Idempotently create/update configured regions |
| `firewatch sources-check --region demo-region` | Validate access, units, date coverage, and sanitized source status |
| `firewatch import-firms --path data/raw/archive.csv --manifest data/raw/archive.manifest.json` | Validate/import archived detections with provenance |
| `firewatch prepare-weather --region demo-region --start 2025-06-01 --end 2025-06-30 --product historical-forecast` | Populate historical weather cache; report gaps |
| `firewatch build-baselines --region demo-region --config configs/features-v1.yaml` | Populate fixed-reference climate statistics |
| `firewatch build-dataset --region demo-region --start 2025-06-02 --end 2025-06-29 --config configs/model-v1.yaml` | Produce past-only features, future labels, split manifest, and Parquet artifacts |
| `firewatch train --config configs/model-v1.yaml` | Train from declared development partitions and save candidate artifact |
| `firewatch evaluate --version candidate-v1 --partition test` | Produce saved held-out metrics; require a frozen candidate before opening test |
| `firewatch promote-model --version candidate-v1` | Verify and approve a trusted compatible artifact |
| `firewatch refresh --region demo-region` | Queue one durable live refresh and print job ID |
| `firewatch replay --scenario demo-active-v1` | Build/read isolated historical results and print run ID |
| `firewatch verify-alert --alert-id UUID` | Return payload/anchor verification without sending a new transaction |

The dates and region names are examples, not a claim that this period contains useful fire events. Replace them with the inventory selected in Module 2. `model-v1.yaml` must declare dataset paths, split boundaries, target settings, scorer versions, seeds, calibration policy, and output paths. `features-v1.yaml` must declare input units, lookbacks, clustering settings, climate reference years, and missing-value rules. The CLI must fail with a clear message when required data/configuration is absent.

After a completed run exists, demonstrate the backend through Swagger or PowerShell:

```powershell
$apiBase = 'http://localhost:8000'
Invoke-RestMethod "$apiBase/health/ready"
Invoke-RestMethod "$apiBase/api/v1/regions"
# Replace RUN_UUID with the completed run ID printed by replay/refresh.
Invoke-RestMethod "$apiBase/api/v1/hotspots?run_id=RUN_UUID&limit=10"
Invoke-RestMethod "$apiBase/api/v1/summary?run_id=RUN_UUID"
```

No frontend is required to pass these checks. For authentication examples, read the operator token from a secret environment variable and never paste a real token into documentation or saved shell history.

## 13. API response contract for the future frontend

Illustrative schema example only; numbers below are not measured model results or real incident evidence.

```json
{
  "schema_version": "api-v1",
  "run_id": "00000000-0000-4000-8000-000000000001",
  "mode": "replay",
  "as_of": "2025-06-10T12:00:00Z",
  "data_completeness": "partial",
  "items": [
    {
      "snapshot_id": "00000000-0000-4000-8000-000000000002",
      "latitude": 34.12,
      "longitude": 71.55,
      "latest_detection_at": "2025-06-10T11:30:00Z",
      "priority": "HIGH",
      "priority_score": 0.72,
      "score_kind": "baseline",
      "escalation_probability": null,
      "scorer_version": "baseline-v1",
      "feature_version": "features-v1",
      "reasons": ["Strong FRP increased the configured priority index"],
      "quality_flags": ["climate_baseline_missing"],
      "alert_id": null
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

`priority_score` is a version-specific ranking value, not interchangeable across scorers. A probability is populated only when the release supports that claim. Alerts are generated for Critical records by default; changing that policy must be versioned. Summary counts must use the same run and filters as the list.

Suggested API error envelope:

```json
{
  "error": {
    "code": "SOURCE_UNAVAILABLE",
    "message": "The latest satellite refresh failed; previous results remain available.",
    "request_id": "public-correlation-id",
    "retryable": true
  }
}
```

Log internal stack traces server-side with the correlation ID; do not return credentials or provider request URLs.

## 14. Required test and acceptance matrix

| Scenario | Expected behavior | Owner |
|---|---|---|
| Same archive imported twice | No duplicate observations; lineage retained | 2 |
| FIRMS returns HTML/error/empty CSV | Correctly distinguish failure from no detections | 2 |
| Acquisition `0037` | Parsed as 00:37 UTC | 2 |
| Past replay with current services online | Uses historical cache only | 3 |
| Future detection exists in database | Not included in features at earlier T | 3/4 |
| Missing weather hour | Incomplete rainfall; null/flag, no silent zero | 3 |
| POWER fill sentinel | Missing value, not extreme weather | 3 |
| Boundary lat/lon and 2 km radius | Correct coordinate/distance semantics | 2/3 |
| Same fire around a split boundary | Purged/grouped away from leakage | 4 |
| No future observation | Weak proxy negative or excluded under documented sensitivity analysis | 4 |
| Baseline/unvalidated model | Probability null; scorer labeled | 1/4 |
| Explanation with imputed weather | No invented observed-weather claim | 4 |
| Worker crashes after partial writes | Lease recovery without duplicate run/alerts | 3/5 |
| Two refresh submissions with same key | One durable job | 1/3 |
| Invalid/missing operator token | Request rejected | 1 |
| Payload copied and one field changed | Integrity mismatch | 5 |
| Chain RPC offline | Alert still available; verification unavailable/pending | 5 |
| Empty completed live run | Empty result with valid source status | 1/3 |
| Container restart | Data, models, and queued jobs retained | 5 |
| Restored backup | Previously saved evidence still verifies | 5 |

Performance targets are acceptance goals to measure on the chosen server: cached regional read endpoints p95 under 1 second at modest demo load; baseline replay of the small prepared bundle under 60 seconds; live refresh completes before the next scheduled interval under normal provider availability. Record actual dataset size, concurrency, hardware, and timings. These are not achieved results or provider SLAs.

## 15. Final end-to-end completion checklist

- [ ] Clean clone, dependency installation, migration, and seed commands succeed.
- [ ] Real FIRMS live ingestion runs on schedule and publishes source freshness.
- [ ] Historical fire and matching historical weather are imported with checksums.
- [ ] Climate baseline definitions and reference period are recorded.
- [ ] Feature computation is past-only and shared across live/train/replay.
- [ ] At least one real-data completed replay works without internet access.
- [ ] Baseline and supervised results are compared honestly, or baseline-only limitations are documented.
- [ ] Probability claims, priority thresholds, and explanations match released scorer metadata.
- [ ] Model card, data manifest, source credits, and evaluation report exist.
- [ ] Ranked list/detail/summary/GeoJSON/status APIs are tested and documented.
- [ ] Operator endpoints are authenticated, bounded, and idempotent.
- [ ] Evidence canonicalization and tamper demonstration pass.
- [ ] Optional blockchain status is represented accurately; failures do not block results.
- [ ] CI, migration tests, worker recovery tests, and end-to-end replay pass.
- [ ] HTTPS deployment survives restart with persistent storage.
- [ ] Backup restore and application/model rollback procedures are verified.
- [ ] Frontend handoff includes OpenAPI and examples, without operator secrets.
- [ ] Required research disclaimer is included in API metadata/documentation for the future frontend.
- [ ] Actual hackathon submission rules, track requirements, and blockchain expectations are checked against the current official event page.

Required product wording for the future frontend and backend documentation:

> Research and decision-support prototype only. Satellite thermal anomalies and model priority scores can be wrong or incomplete. Do not use FireWatch AI for emergency response, preservation of life, or protection of property. Confirm important events with official authorities and operational fire services.

## 16. Documentation and release deliverables

Each module owner contributes to these shared artifacts before final release:

1. `README.md`: exact local setup, commands, environment configuration, and first replay.
2. `docs/contracts.md`: DTOs, internal interfaces, REST behavior, and error semantics.
3. `docs/data-dictionary.md`: fields, units, missing values, source products, and provenance.
4. `docs/model-card.md`: target, splits, proxy limitations, metrics, calibration, and supported region.
5. `docs/runbook.md`: deploy, restart, refresh, recover, backup/restore, and rollback.
6. `docs/frontend-handoff.md`: read API examples, GeoJSON, priority/verification enums, timestamps, and auth boundaries.
7. Replay bundle manifest, approved model bundle, checksums, and reproducible evaluation report.
8. Release manifest connecting code SHA, image digest, migration revision, model/feature/evidence versions.

Backend completion means these deliverables and acceptance gates pass. A working `/docs` page alone is not sufficient.

## 17. Official implementation references

The initial project direction comes from the supplied FireWatch AI Hackathon Documentation PDF. This roadmap resolves implementation choices and adds backend-specific contracts; it does not certify the event rules or claim any model has already been trained.

- **[S1] NASA FIRMS Area API:** endpoint structure, bbox format, products, date availability link, request limits. https://firms.modaps.eosdis.nasa.gov/api/area/
- **[S2] NASA FIRMS Archive:** historical downloads, access, sensor temporal coverage, and product replacement notes. https://firms.modaps.eosdis.nasa.gov/download/
- **[S3] NASA FIRMS MAP_KEY:** free registration and transaction limits. https://firms.modaps.eosdis.nasa.gov/api/map_key/
- **[S4] Open-Meteo Terms:** non-commercial service limits, permitted usage, attribution/license conditions. https://open-meteo.com/en/terms
- **[S5] Open-Meteo Historical Forecast API:** archived model products, time coverage, and relationship to live forecast data. https://open-meteo.com/en/docs/historical-forecast-api
- **[S6] Open-Meteo Forecast / Historical Weather:** live variable semantics and retrospective reanalysis alternative. https://open-meteo.com/en/docs and https://open-meteo.com/en/docs/historical-weather-api
- **[S7] FastAPI Docker Deployment:** building and operating an application container. https://fastapi.tiangolo.com/deployment/docker/
- **[S8] Docker Compose Startup Order:** health checks and dependency readiness. https://docs.docker.com/compose/how-tos/startup-order/
- **[S9] scikit-learn Probability Calibration:** calibration concepts and evaluation. https://scikit-learn.org/stable/modules/calibration.html
- **[S10] Caddy Automatic HTTPS:** public TLS prerequisites and certificate behavior. https://caddyserver.com/docs/automatic-https
- **[S11] NASA POWER Daily API:** daily requests and parameter conventions. https://power.larc.nasa.gov/docs/services/api/temporal/daily/
- **[S12] NASA POWER API Tutorial:** free climate-data access and API guidance. https://power.larc.nasa.gov/docs/tutorials/service-data-request/api/
- **[S13] Solidity Contracts:** contract/state/event concepts; choose a released compiler version for implementation. https://docs.soliditylang.org/en/latest/contracts.html
- **[S14] Event page:** verify current submission requirements separately. https://ieee-climatechain-hack.devpost.com/

The exact model target, thresholds, clustering settings, table design, ownership split, and deployment sizing in this plan are proposed engineering decisions. Validate them through the phases above rather than treating them as facts supplied by the data providers.
