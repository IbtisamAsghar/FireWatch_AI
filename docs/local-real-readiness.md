# Install and restore the historical backend

The delivered database and readiness receipts describe historical research
replays on the original host. They do not imply that an API is running on a new
clone. The original export contains 19,771 source observations, 288 weather
records, 48 climate references and the recorded approved historical model.

## Fresh Windows installation

Use Python 3.12. Run from the repository root. The runtime and credentials are
kept under Local AppData, outside the checkout and OneDrive. No machine-wide
long-path setting is required for managed-release verification: Python artifact
I/O uses Windows extended paths. Git still needs `core.longpaths=true` for checkout.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv\Scripts\python.exe scripts/prepare_local.py --restore
.\.venv\Scripts\firewatch.exe check-config
.\.venv\Scripts\firewatch.exe db-check
```

`--restore` provisions the isolated `PostgreSQL16-devtalha` instance on localhost
55432 and restores the delivered dump into its **empty** application database.
It refuses a nonempty database and never uses `pg_restore --clean`. The restore
is one transaction. Original alert payloads, completed runs, scores, approval
records and the final-test ledger are preserved. Source storage locators are
rebased only when the destination file matches its original SHA-256. Sources
outside the existing audit roots are copied byte-for-byte into a content-addressed
`data/raw/restored-assets` cache; unavailable files remain explicitly unavailable. No historical evidence bytes are rewritten.
The script selects the restored registry and the delivered readiness replay catalog.
Existing environment files selecting another database are preserved and rejected.

For a new empty baseline installation, omit `--restore`, then seed regions with
`firewatch seed-regions --config configs/regions.yaml`. Choose the empty or restore
workflow before the first migration: a database already containing tables cannot
be used as a restore target. Use a separate `--home`/`--port` and matching private
environment when provisioning another instance. Never overwrite an existing DB.

## Start after installation or restart

```powershell
.\.venv\Scripts\python.exe scripts/native_postgres.py start --home "$env:LOCALAPPDATA\FireWatch\PostgreSQL16-devtalha"
.\.venv\Scripts\python.exe -m uvicorn firewatch.main:app --host 127.0.0.1 --port 8000
```

Use a second terminal for the replay-only job worker:

```powershell
.\.venv\Scripts\python.exe -m firewatch.worker --no-schedule
```

The compatibility `prepare_training_database.py` now delegates to the same
portable helper; it no longer assumes an excluded `.local/phase52` runtime.
The restart PowerShell script derives its workspace from its own location and
checks process ownership before stopping services. It needs a configured `.env`.

## Verify the restored historical results

Check `/health/ready` and `/api/v1/models/current`. The recorded ML run ID is
`b3f9c88e-d4e2-501b-85b1-8fea26048fb4`. Use it consistently for:

```text
/api/v1/hotspots?run_id=b3f9c88e-d4e2-501b-85b1-8fea26048fb4
/api/v1/hotspots.geojson?run_id=b3f9c88e-d4e2-501b-85b1-8fea26048fb4
/api/v1/summary?run_id=b3f9c88e-d4e2-501b-85b1-8fea26048fb4
```

The original results have six hotspots, real enrichment and `partial` coverage
because a cluster exceeds target geometry. Preserve that warning. The model is
uncalibrated; probability remains null. It emitted no alert evidence for this run.
The baseline run `63c1b8df-4f31-544d-9691-16f8ef39f9bf` retains two evidence records.
Use `verify_artifacts=true` to audit the original pinned sources in addition to
payload hashes. Operator submissions need the private server token and an
idempotency key; keep the token out of browser code.

## Database-backed regression

The private runtime provisions a separate `firewatch_test` database. Run:

```powershell
.\.venv\Scripts\python.exe scripts/test_local.py
.\.venv\Scripts\python.exe scripts/verify_local_delivery.py --replay
```

The test runner reads private configuration without printing credentials and
selects only `firewatch_test`; outbound provider access is blocked. The delivery
verifier checks readiness, the full managed release, list/map/summary consistency,
all evidence artifacts and an idempotent worker replay. Its replay flag admits
one job and refuses to consume unrelated queued work.
Never point tests at the restored application database. Test schema cleanup
refuses a database whose name does not end in `_test`.

The old [readiness receipt](local-real-readiness-receipt.json) and committed XML
reports remain original-host evidence. Fresh repair validation is recorded
separately in [delivery repair validation](delivery-repair-validation.md).
Live NRT/new-region validity, production HTTPS, backup operations and deployment
monitoring remain separate acceptance work; no public deployment is implied.
