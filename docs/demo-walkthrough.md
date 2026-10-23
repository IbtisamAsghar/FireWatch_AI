# Synthetic Swagger acceptance demo

This is a software handoff fixture, not an ingested fire dataset or a trained
prediction. It demonstrates all 15 read operations over real migrated PostgreSQL.
All coordinates, weather values, priorities and evidence are invented. Scores are
precomputed fixtures, not an implemented scorer. The source is the committed
`data/sample/synthetic-demo-v1.json`, generated for this repository under its MIT
license. No NASA/Open-Meteo observation or provider availability is claimed.

## Start and stop

Use the README's native setup first. Run from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts/native_postgres.py start
.\.venv\Scripts\python.exe -m scripts.demo_api --port 8001
```

Open `http://127.0.0.1:8001/docs`. The title/banner says **SYNTHETIC acceptance
demo**. Example UUIDs are prefilled in OpenAPI; click Try it out and Execute.
The protected job GET still requires the server operator token through Swagger's
Authorize control. Local `.env` credentials remain private; the tool never prints
or bundles the token. Use your existing local operator token privately. Do not put
it in frontend code or handoff artifacts. When an explicitly supplied test DSN is
used without any server configuration/token, automation uses an ephemeral token;
for an interactive session provision `ADMIN_API_TOKEN` privately as usual.

Ctrl+C gracefully stops the API and removes only its own temporary schema. The
normal application API on port 8000 is unaffected. Demo startup creates a fresh
random `fw_demo_<32 hex>` schema in `firewatch_test`, migrates from empty to head,
checks migration/model drift, seeds configured regions twice, and loads the demo
twice to verify idempotency. The application database/public schema is not seeded
with synthetic results. Production configuration and databases without a `_test`
suffix are refused. Existing unrelated schemas are never removed.

For another PostgreSQL installation, privately provide a dedicated `_test` DSN in
`FIREWATCH_TEST_DATABASE_URL`; do not pass passwords as command-line arguments.
The role needs schema creation in that disposable database, not server-admin rights.
A forced process kill can prevent normal cleanup; do not remove unrelated schemas
to recover a demo. Normal Ctrl+C and automated verification exercise graceful cleanup.

Every POST/PUT/PATCH/DELETE is blocked with 403 in the demo. This prevents Swagger
from accidentally queuing work even when provider/chain credentials exist locally.
Use the regular API and [operator API](operator-api.md) for genuine job admission.
No provider, archive download, model loading, worker or chain RPC is called here.

## Dataset and example IDs

IDs are deterministic fixture UUIDs. The demo clock is fixed at
2025-06-11T12:03:00Z; times do not imply current source freshness.

- Region: `a715a16e-58f7-535f-b4f3-bc0149583c54`, named `synthetic-demo-v1`, disabled
  for real refresh admission. Configured regular regions are also seeded separately.
- Completed live run: `a7c0e61b-b5b7-5450-baa3-94e0a5ff38a0`, cutoff June 11 12:00Z.
- Completed replay run: use the UUID shown by `/api/v1/replays` or demo-index.json;
  cutoff June 10 12:00Z, with a separate historical snapshot.
- Empty completed replay and an unavailable queued live run cover empty/503 states.
- Live critical snapshot: `bd90ec0b-6eb3-5081-a831-14f642bca0ab`; FRP 42 MW,
  temperature 32 degC, wind 5 m/s, no escalation probability. Its evidence is
  explicitly synthetic and verifies against its saved original bytes.
- A second live snapshot has missing FRP/weather; fields stay null, not zero.
- Alert: `c877bd15-2f51-5ebe-b0d8-f707f4919a15`.
- Queued synthetic job: `e0e632c5-4874-555a-a2a7-0cf23fc8251c`; this is a sample
  progress record, not a claim that an executor exists.

## Demonstrate all reads

1. `/health/live`: successful process-only liveness.
2. `/health/ready`: database revision 0003 ready; no model artifact required for baseline reads.
3. `/api/v1/regions`: bounding boxes, completed modes and published live head.
4. `/api/v1/status`: separate source retrieval/observation times and honest scorer status.
5. `/api/v1/runs`: use the prefilled region; compare live/replay cutoffs and queued metadata.
6. `/api/v1/runs/{run_id}`: stages, versions, completeness, timestamps.
7. `/api/v1/hotspots`: the prefilled live run ranks two snapshots; try `limit=1`,
   `offset=1`, or `priority=CRITICAL&bbox=70,33,72,35`.
8. `/api/v1/hotspots/{snapshot_id}`: explicit feature units, missing flags and saved evidence.
9. `/api/v1/hotspots.geojson`: first point `[71,34]` is longitude then latitude;
   filters match ranking and summary; bounded truncation metadata is explicit.
10. `/api/v1/summary`: live total two; the critical/bbox filter produces total one.
11. `/api/v1/models/current`: baseline metadata; scoring_available remains false.
12. `/api/v1/replays`: the completed synthetic historical run; never a live fallback.
13. `/api/v1/alerts/{alert_id}`: saved synthetic evidence projection and original digest.
14. `/api/v1/alerts/{alert_id}/verify`: local_match/unanchored; no transaction is sent.
15. `/api/v1/jobs/{job_id}`: authorize privately; missing/wrong token returns 401.

Request the replay's snapshots and then request a live snapshot/alert with that
replay's `run_id`: the scoped detail returns 404. Request the empty run: collections
stay empty with zero totals. Unknown UUIDs produce 404, invalid filters 422, and
the queued run's output produces retryable 503. Error bodies include a matching
server-generated X-Request-ID. The regular API's integration tests separately prove
idempotent operator writes and concurrent admission without weakening this demo.

## Automatic verification and handoff

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_demo
.\.venv\Scripts\python.exe -m scripts.export_handoff
```

The verifier runs a loopback Uvicorn server, calls every read over actual HTTP,
checks operator rejection/Swagger, and shuts down gracefully with schema cleanup.
The exporter calls the actual API against its own isolated schema and writes:

- `artifacts/handoff/openapi.json`: actual 18-operation production API schema.
- `examples.json`: successful responses for every read, replay/empty examples,
  and actual 401/404/422/503 structured errors.
- `field-dictionary.json`: all request/response/enum component schemas, nullable
  types, defaults, required fields and unit notes/descriptions.
- `demo-index.json`: example UUIDs and exact GET paths.
- `manifest.json`: API/contracts/migration/fixture versions and SHA-256 checksums.
- `README.md` and `docs/`: usage and human-readable API/security/unit contracts.

CI publishes this directory as `firewatch-frontend-handoff`, alongside the existing
`firewatch-openapi` artifact. No operator token or database URL is included.
Correlation IDs and ordinary seeded-region UUIDs can vary; fixtures and behavior
are reproducible, without promising byte-identical response exports.

See [frontend handoff](frontend-handoff.md) and [operator API](operator-api.md).
The required research limitation applies: do not use this prototype for emergency
response, preservation of life or protection of property.
