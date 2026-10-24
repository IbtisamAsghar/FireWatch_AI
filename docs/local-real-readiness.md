# Run and test the local backend with real data

The API is available at `http://127.0.0.1:8000`; interactive documentation is
`http://127.0.0.1:8000/docs`. All described result data comes from actual provider
observations. Results are historical research replays, not current live fires.

## Ready now

- 19,771 NASA FIRMS source observations, 288 Open-Meteo hourly weather records
  and 48 NASA POWER monthly climate references are stored locally.
- Four historical runs are published. The active ML run has six
  hotspots with real weather and climate. The enriched baseline run has six
  hotspots, two evidence records and real temperature, humidity, wind, gust,
  24-hour rainfall, temperature departure and rainfall deficit on all hotspots.
- Map, ranked list, summary and details agree. Both evidence payloads passed local
  hash checks. A quiet scenario correctly returns zero hotspots.
- Local frontend origins on ports 3000 and 5173 are allowed. Operator submission
  still requires the server token; never put that token into browser code.
- Full regression: 1,273 passed, one Windows symlink skip. Strict mypy and Ruff
  passed. These tests use disposable data; the serving replays use real data.

The trained-ML run ID is `b3f9c88e-d4e2-501b-85b1-8fea26048fb4`.
Use it consistently for these GET requests:

```text
/api/v1/hotspots?run_id=b3f9c88e-d4e2-501b-85b1-8fea26048fb4
/api/v1/hotspots.geojson?run_id=b3f9c88e-d4e2-501b-85b1-8fea26048fb4
/api/v1/summary?run_id=b3f9c88e-d4e2-501b-85b1-8fea26048fb4
```

Open `/api/v1/hotspots/{snapshot_id}` using a returned snapshot ID. The response
has a flat `features` mapping, explicit `units` and `missing_flags`.
`artifacts/enriched-real-ml-api-examples.json` contains verified ML responses.
`ACTIVE_SCORER=registry` selects the approved `forest-independent-2025-v2`.
Talha confirmed review and approval for historical replay with limitations
acknowledged. The authenticated HTTP submission, dedicated worker, matching
map/list/counts, operator authentication and retry idempotency all passed.
The ML priority thresholds emitted no alert evidence in this run; baseline
evidence records remain available under the preserved baseline run
`63c1b8df-4f31-544d-9691-16f8ef39f9bf`. Historical scores are unchanged.

The populated run remains `partial` because one satellite cluster extends beyond
the target radius. All requested weather/climate fields are present. This warning
must remain visible; it must not be removed to display a misleading Complete badge.

## Start after a restart

From the repository root:

```powershell
.\.venv\Scripts\python.exe .local/model-training/prepare_training_database.py
.\.venv\Scripts\python.exe -m uvicorn firewatch.main:app --host 127.0.0.1 --port 8000
```

Use a second terminal for the replay-only job worker if queue submission is needed:

```powershell
.\.venv\Scripts\python.exe -m firewatch.worker --no-schedule
```

The private `.env` points to the isolated PostgreSQL 16 database on localhost port
55432 and the new replay catalog. Data persists under `.local/model-training/`;
raw evidence, bundles and models persist under `data/`. The original PostgreSQL
service on 5432 was not modified. No public deployment is claimed.

## Remaining boundaries

This is ready for local frontend integration with real historical data. Trained
ML is active for the approved historical scope. Live NRT
generalization, production hosting/HTTPS, monitoring and backup/restore acceptance
remain separate work. See [the verified receipt](local-real-readiness-receipt.json).
