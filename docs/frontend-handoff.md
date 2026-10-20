# Frontend read API: api-v1

Phase 1.4 implements all 15 planned GET operations. Phase 1.5 adds three protected
POSTs; see [operator API](operator-api.md). Run the API using the Windows
commands in the README; local base URL is `http://127.0.0.1:8000`. Explore `/docs`
and `/openapi.json`. Generate the identical contract without starting services:

```powershell
.\.venv\Scripts\python.exe scripts/export_openapi.py
```

GitHub Actions publishes `artifacts/openapi.json` in the `firewatch-openapi`
artifact on successful backend Linux checks. The export needs no environment
file, database, provider credentials or model file. Operation IDs are explicit
and stable. Query/response DTOs live in `src/firewatch/schemas/api.py`.

## Access policy

The local research prototype deliberately permits sanitized business reads with
`PUBLIC_READS_ENABLED=true` (default); the server still binds to localhost using
the README command. Set it to `false` for a private deployment: every `/api/v1`
read then requires the operator bearer token. Health routes remain public.
Production defaults private when the setting is absent; explicit true opts into
public reads.
OpenAPI describes the two deployment alternatives for business reads.
`GET /api/v1/jobs/{job_id}` always requires `Authorization: Bearer <operator-token>`.
Never put this token in frontend source, browser storage, screenshots, or URLs.
Operator jobs remain a server-side tool until a real login system exists.

CORS uses only explicit `CORS_ORIGINS`; its default is an empty allowlist.
Authorization and Idempotency-Key headers and GET/POST preflights are permitted
for approved origins; credentialed cookies are disabled. Phase 1.5 adds the three
authenticated server-side operator mutations with required
idempotency keys. They queue durable work; executors remain later modules.

## Reproducible Module 1 handoff

Run `python -m scripts.demo_api --port 8001` for an isolated synthetic Swagger demo;
see [walkthrough](demo-walkthrough.md) for all 15 reads and safe cleanup. Regular
application data stays untouched. `python -m scripts.export_handoff` generates
`artifacts/handoff` with the real 18-operation OpenAPI, actual sample/error responses,
complete machine field dictionary, demo UUID index, human docs and version/checksum
manifest. CI publishes `firewatch-frontend-handoff`. Synthetic fixture scores are
not predictions, and no token is included. See [Phase 1.6 acceptance](phase-1.6-checklist.md).

## Choose a run explicitly

1. `GET /api/v1/regions` lists region UUIDs, bounding boxes, actual available
   completed-run modes, and the last published live run UUID.
2. `GET /api/v1/runs?region_id=<uuid>&mode=live` lists completed/failed history.
   `mode=replay` requests historical runs; `status` optionally selects a status.
3. Use an immutable completed `run_id` for list, summary, and GeoJSON requests.
   A replay request never falls back to live data. Detail endpoints infer their
   immutable run from the record; optional `run_id` enforces an additional scope.
4. `GET /api/v1/replays` lists completed replay runs with saved scenario metadata,
   not invented demo scenarios. Producers save `replay_scenario` with a slug `id`
   and optional `name`/`description` in the run input manifest before completion.

A fresh installation contains a disabled illustrative region and no processed
runs. Region modes and replay collections are empty until later ingestion and
pipeline modules publish actual results. Integration tests use explicitly
synthetic isolated data and never populate the application database with it.

## Read endpoints and filters

- `/health/live`: process-only `{"status":"ok"}`; makes no dependency calls.
- `/health/ready`: checks database revision `0002` and required local artifact.
- `/api/v1/status`: source attempts/successes, last published live refresh,
  forecast-cache retrieval time, climate-record count, and scorer availability.
- `/api/v1/runs/{run_id}`: configuration hash, versions, stages, status,
  completeness and times. Running/failed metadata is readable; their output
  collections return `503 RUN_NOT_READY`.
- `/api/v1/hotspots?run_id=<uuid>`: ranked snapshots. Optional `priority` is one
  of `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`. Optional `bbox=west,south,east,north`
  uses inclusive WGS84 degrees; crossing the antimeridian is unsupported.
- `/api/v1/hotspots/{snapshot_id}`: coordinates, aggregates, feature values,
  missing flags, units, explanation, versions and saved evidence when present.
- `/api/v1/hotspots.geojson?run_id=<uuid>`: GeoJSON FeatureCollection; identical
  ranking/priority/bbox selection. Point coordinates are **[longitude, latitude]**.
- `/api/v1/summary?run_id=<uuid>`: total, zero-filled priority/score-kind counts;
  accepts the same priority/bbox filters. Counts cover the full filtered dataset.
- `/api/v1/models/current`: configured scorer/version, probability capability,
  artifact readiness and limitations; never returns model paths or manifests.
- `/api/v1/alerts/{alert_id}` and `/verify`: saved evidence projection and
  read-only integrity/anchoring status.
- `/api/v1/jobs/{job_id}`: authenticated progress, result run, stage status and
  a sanitized generic failure message. Payloads and raw errors stay private.

Example requests (replace UUIDs with an actual completed run):

```text
/api/v1/hotspots?run_id=<uuid>&limit=50&offset=0&priority=HIGH&bbox=70,33,74,36
/api/v1/summary?run_id=<uuid>&priority=HIGH&bbox=70,33,74,36
/api/v1/hotspots.geojson?run_id=<uuid>&priority=HIGH&bbox=70,33,74,36
```

Paginated lists default to `limit=50`, maximum 200. GeoJSON defaults to and caps
at 2,000 features. All offsets are nonnegative and bounded at 1,000,000.
Ranking is descending score, then ascending snapshot UUID; immutable completed
runs keep pagination stable. Responses include `total`, `limit`, `offset` where
paginated; GeoJSON adds `returned` and `truncated`. `truncated=true` means the
response omits part of the selection, including an offset that skips earlier
items. A limit over the cap is rejected rather than silently changed.
Valid completed runs without detections return empty arrays and zero counts.
Do not interpret an empty result as a dependency failure or proof of safety.

## Times, units, missing values and freshness

Run-bound results include `run_id`, `mode`, `as_of`, `data_completeness` and
`freshness`. Times serialize as timezone-aware UTC ISO 8601 with `Z`. Coordinates
are WGS84 degrees, FRP is MW, and feature-specific units are carried explicitly.
Null means unknown; never render missing values as zero. `priority_score` is a
ranking value, while `escalation_probability` stays null for baseline/model-score
outputs. `score_kind` distinguishes `baseline`, `model_score`, and `probability`.

Freshness uses only that run's immutable outputs and saved manifest metadata.
Replay age is measured against its historical cutoff; live age uses current UTC.
Latest observation/scoring times are calculated across the entire selected run,
independent of page/bbox/priority. Optional producer-owned manifest `freshness`
fields are `latest_weather_valid_at` and `latest_retrieved_at`, as aware ISO times.
Unavailable metadata stays null with quality flags; future weather beyond the
run cutoff is ignored. No current global weather cache is substituted into replay.
Observation stale threshold is 24 hours; weather uses `WEATHER_MAX_AGE_HOURS`.

Source status distinguishes `never_fetched`, `ok`, `stale`, `degraded`,
`unavailable`. Freshness there measures successful retrieval age (24-hour
threshold); a fresh valid empty source response can be `ok` with null latest
detection. A failed retry preserves the previous successful retrieval and marks
the source `degraded`; no success means `unavailable`. NOAA-20 and NOAA-21 NRT
are reported separately. This endpoint does not fetch from providers.

The baseline requires no artifact, so database-ready reads can be ready while
`scoring_available=false` and baseline status is `not_implemented`. Scoring is
a later module. Other configured scorers require an approved matching feature
version and a SHA-256-verified file inside `MODEL_DIR`; no model is deserialized.
`ready` model status means the local artifact is ready, not validated accuracy.

## Evidence and verification

Alert payloads are explicitly a `public_projection`: credential-bearing keys,
URLs, paths and raw source manifests are removed/redacted, with bounded depth
and sizes. `payload_redacted` indicates a changed projection. Saved canonical
bytes remain untouched; `original_hash` refers to those bytes, **not** the public
projection. `/verify` recomputes SHA-256 over the original stored bytes and reports
`local_match` or `local_mismatch`. It does not verify external source/model files
(`artifact_verification=not_performed`) or certify the scientific claims.

Anchor states are `unanchored`, `anchor_pending`, `anchor_match`,
`anchor_mismatch`, `anchor_unavailable`. Queued/submitted outbox rows are pending.
Saved receipt metadata alone never establishes `anchor_match`: a trusted read-only
chain adapter must retrieve and match the digest. No RPC adapter is implemented
in this phase, so existing receipts report unavailable until Module 5 integrates
one. Adapter outages/invalid responses stay unavailable, not a false mismatch.
Verification GETs cannot enqueue work, sign transactions or change evidence;
all database-backed read routes enforce PostgreSQL read-only transactions.

## Errors

Unknown UUIDs: 404. Invalid UUIDs, enums, unknown query fields, bounds or filters:
422. Database/schema/artifact unavailability or incomplete requested output:
503. Missing/wrong authentication: 401 with `WWW-Authenticate: Bearer`.
Errors use the following envelope, also declared in OpenAPI:

```json
{"error":{"code":"RUN_NOT_FOUND","message":"The requested run was not found.","request_id":"server-generated-uuid","retryable":false}}
```

`X-Request-ID` correlates every response. Raw SQL, provider failure text,
authorization headers, DB credentials and model paths are excluded from responses
and error logs. Unexpected errors use a generic 500 response. Retry only where
`retryable=true`; do not replace a missing replay with a live response.

Research and decision-support prototype only. Satellite thermal anomalies and model
priority scores can be wrong or incomplete. Do not use FireWatch AI for emergency
response, preservation of life, or protection of property. Confirm important events
with official authorities and operational fire services.
