# Phase 5.6: local container backend

This is a local Linux-container stack, separate from the native Windows database
and `.env`. No frontend, synthetic observations, public hostname or trained model
is required to start it. Docker Desktop (Linux containers) or a Linux Docker
engine with Compose 2.24+ is required. Configuration was validated with official
Compose 5.6.1; actual engine acceptance is a separate check.

## Start and inspect

Run from the repository root using the existing locked Python 3.12 environment:

```powershell
.\.venv\Scripts\python.exe -m scripts.compose_stack init
.\.venv\Scripts\python.exe -m scripts.compose_stack up --build
.\.venv\Scripts\python.exe -m scripts.compose_stack status
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

`init` creates private `.env.compose` with separate random bootstrap, application
database and operator secrets. It preserves existing files and never reads/copies
the native `.env`. POSIX permissions are 0600; Windows generation restricts its
ACL to the current user. Keep this file private and backed up with the volumes.
An example is committed as `.env.compose.example`; do not use its placeholders.
The helper explicitly selects this env file and gives its values precedence over
similarly named shell variables, avoiding accidental native/Compose credential mixing.

`up --build` builds the shared image, quiesces existing API/worker writers, waits
for PostgreSQL health, prepares volume-root permissions, runs `alembic upgrade
head` once as the release command, seeds region configuration, and starts API and
worker with health checks. A second `up` is an explicit new release/start operation
and repeats the idempotent migration/seed commands. Ordinary container restarts
never migrate. `up` without `--build` reuses the image. Use the helper for initial
startup and updates rather than starting writers before migration.

Open [Swagger](http://127.0.0.1:8000/docs). Only the API port is published, bound to
127.0.0.1. If occupied, set `COMPOSE_API_PORT` in `.env.compose`, rerun `up`, and
use that port. The database does not expose a host port and can coexist with
native PostgreSQL. No service installs a scheduler inside Uvicorn.

For logs and trusted operator commands:

```powershell
docker compose --env-file .env.compose logs --tail 100 api worker
docker compose --env-file .env.compose run --rm operator firewatch db-check
```

Do not print resolved `docker compose config`: it contains interpolated secrets.
Use `config --quiet` for validation. The helper captures command output on failure
to prevent accidental configuration disclosure; inspect service logs locally.

## Services and access

| Service | Purpose and privileges |
| --- | --- |
| `db` | Digest-pinned PostgreSQL 16.15, private database network, persistent PGDATA |
| `storage-init` | Short root init with CHOWN/FOWNER only, no network/secrets; changes volume roots to UID/GID 10001, never recursively changes existing artifacts |
| `migrate`, `seed` | Explicit one-shot release tools; migrations do not run in API requests |
| `api` | UID/GID 10001, read-only root/config/raw/model/replay mounts, no FIRMS key or chain signing key |
| `worker` | Same image/UID; raw cache writable, model/replay/config mounts read-only; dedicated scheduler/consumer |
| `operator` | Opt-in trusted tools with write access to managed raw/model/replay volumes and read-only `/imports` |
| `anvil`, `anchor-worker` | Optional `chain` profile; node has no host RPC port, signer key only enters anchor-worker |
| `caddy` | Optional `proxy` profile; local HTTP reverse proxy with persistent data/config volumes |

PostgreSQL initialization creates `firewatch` as NOSUPERUSER/NOCREATEDB/
NOCREATEROLE/NOREPLICATION, owning only the application database. Bootstrap
credentials enter only the DB service. Initialization runs only for a new PGDATA;
changing the env file does not rotate an existing role's password. Restore the
correct env file or perform an explicit database password rotation; never delete
volumes to work around a credential mismatch.

The application image uses a digest-pinned Python base and hashed package locks.
Only the installed wheel, migrations, configs and runtime dependencies are in
the final stage. Raw/history archives, private model bundles and `.env` files are
excluded. The app root is read-only, temporary storage is bounded, Linux
capabilities are dropped and `no-new-privileges` is enabled. API and worker
commands remain `uvicorn firewatch.main:app --host 0.0.0.0 --port 8000` and
`python -m firewatch.worker`, through the startup entrypoint.

## Readiness, retries and stopping

Compose DB health controls dependency startup. The Python entrypoint separately
retries database/schema readiness for `STARTUP_TIMEOUT_SECONDS` (default 120),
with `STARTUP_RETRY_SECONDS` between failures. Individual database attempts are
also bounded by the connection timeout. Only migration commands explicitly use
`--database-only`; all serving/worker commands require revision 0003. Failures
report safe messages without DSNs. SIGTERM/SIGINT stops waiting; successful
startup execs the real process so signal handling remains effective.

The API health check uses `/health/ready`; liveness remains independent of DB
availability after startup. Worker health checks DB/schema and required storage
access. Docker tracks process lifetime; this check is not proof of queue progress.
The worker retains its own database outage polling, lease/heartbeat recovery and
fenced job writes. Inspect job progress separately. `unless-stopped` restarts
long-lived services; Docker health alone does not restart an unhealthy process.

```powershell
.\.venv\Scripts\python.exe -m scripts.compose_stack down
```

This removes containers/networks and retains volumes. `up` recreates services
against the retained data. Worker shutdown allows 120 seconds for current work;
an interrupted job recovers through its existing lease/checkpoint protocol.
Avoid changing the Compose project name unless intentionally creating a separate
stack: volume names are scoped to that project.

## Persistent state and model deployment

Named volumes preserve PostgreSQL, raw/cache data, model releases, replay bundles,
optional Anvil state and Caddy data/config. Runtime paths are fixed under
`/var/lib/firewatch`, so restarting/recreating containers does not change artifact
paths. Source/config binds are read-only. Caddy volumes are used only when that
profile starts. These are durable local disks, not off-machine backups; backup and
restore procedures remain Phase 5.8.

Keep training archives outside the image. To restore an **already reviewed managed
release**, place its versioned directory in ignored `deploy/bundles/`, retain the
separately trusted SHA-256 of `release-manifest.json`, and run:

```text
docker compose --env-file .env.compose run --rm --no-deps --entrypoint python operator -m firewatch.deployment install-model --source /imports/<versioned-release> --release-sha256 <trusted-release-manifest-sha256>
```

Make transfer inputs readable by UID/GID 10001; the import tool does not override
host file permissions. Transfer checks the inventory and inference bundle, bounds bytes/files, stages
the copy, verifies again and atomically installs at
`models/releases/<version>/<bundle-sha>`. A valid repeat is idempotent; changed
files, unsafe paths and conflicting releases are refused. It does not connect
to the registry, invent independent review, grant approval or activate the model.
The registry database and matching managed release must be restored together, or
use the existing reviewed promotion workflow. Do not select registry mode until
its approval and files are ready. `baseline-v1` remains the default.

Windows-native database records may contain Windows artifact paths. Do not assume
copying that database into Linux makes those paths usable. Prefer verified portable
replay packages or fresh ingestion inside this separate stack. See
[replay transfer](deterministic-replay.md) and [model promotion](model-registry-and-promotion.md).

## Real-data use

The initial regions remain disabled. To test real data, privately configure your
FIRMS key in `.env.compose` and first inspect a bounded source refresh:

```powershell
docker compose --env-file .env.compose run --rm operator firewatch refresh-firms --region northern-california --allow-disabled
```

After checking coverage, set only the intended region's `enabled: true` in
`configs/regions.yaml`, rerun the release helper to seed it, and queue a refresh:

```powershell
.\.venv\Scripts\python.exe -m scripts.compose_stack up
docker compose --env-file .env.compose run --rm operator firewatch refresh --region northern-california
```

The running worker now also schedules that enabled region. It uses real provider
responses and saves explicit missing/failure flags; an empty response remains a
valid possible result. Read the job UUID, completed run, hotspots and evidence via
Swagger. No synthetic fire fixtures are loaded by this process. A fresh Compose
DB does not inherit native data or the currently missing real ML candidate files.

## Optional local services

Start the internal Anvil node explicitly:

```powershell
docker compose --env-file .env.compose --profile chain up -d --wait anvil
```

It uses chain 31337, zero pre-funded/public development accounts, a persistent
state volume and internal-only RPC. Registry deployment/funding/signing remain
explicit operator actions using dedicated disposable keys; this phase does not
create a registry or submit an alert. After deployment, configure the actual
registry address, dedicated signer key and `CHAIN_ENABLED=true` privately, then
start `anchor-worker` explicitly and recreate API/worker for the new configuration.
Do not start all `chain` services with empty signer/registry settings.
Native chain config remains loopback-only; Compose explicitly allowlists the
internal hostname `anvil` using `CHAIN_LOCAL_RPC_HOSTS`.

For a local HTTP proxy:

```powershell
docker compose --env-file .env.compose --profile proxy up -d --wait caddy
```

Open `http://127.0.0.1:8080/docs` (or `COMPOSE_HTTP_PORT`). This is local HTTP;
public DNS, HTTPS, production overrides and deployment remain Phase 5.7. This
Compose setup is not a Render deployment blueprint.

## Acceptance

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_compose --optional-services
```

This requires a working Linux Docker engine. It generates private test credentials
and a unique `fwaccept-*` project, builds/starts the actual stack, verifies the
non-superuser role and read-only API mounts, saves a raw/model marker and queued
anchor-job metadata, recreates containers without deleting volumes, and verifies
unchanged identities/data. Optional checks exercise internal Anvil and the HTTP
proxy. No provider credentials, observations, scoring or scientific results are
fabricated. Finally it deletes only that generated acceptance project's volumes.
Failures produce a fixed stage name, never resolved credentials or raw logs.
The sanitized receipt is `artifacts/phase-5.6-compose.json`.

The hosted Linux container job executes this full acceptance and uploads the
receipt. [Local acceptance notes](phase-5.6-checklist.md) distinguish implemented
checks from actually executed Docker validation.
