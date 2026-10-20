# Native Windows development

Use Python 3.12, Git and PowerShell. Docker Desktop and WSL are not required.
Run commands from the repository root. The dependency locks include exact
transitive versions and SHA-256 hashes for Python 3.12 on Windows/Linux. `uv` is
used by maintainers to resolve locks; running the backend requires only pip.

## First setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv\Scripts\python.exe scripts/native_postgres.py setup
.\.venv\Scripts\firewatch.exe check-config
.\.venv\Scripts\firewatch.exe db-check
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\firewatch.exe seed-regions --config configs/regions.yaml
.\.venv\Scripts\firewatch.exe --help
```

Alternatively run `& .\scripts\bootstrap.ps1` from an existing PowerShell
session if local execution policy permits. Do not change global execution policy
to activate a virtual environment; use explicit executable paths above instead.
`-SkipPostgres` installs only the Python environment for an existing PostgreSQL
server. `-PostgresPort 55432` selects an alternate port if 5432 is already used;
the setup helper never stops or modifies an unrelated PostgreSQL installation.

The helper downloads the PostgreSQL 16.15-1 Windows archive from
[EDB's official distribution](https://www.enterprisedb.com/download-postgresql-binaries),
linked by [PostgreSQL's Windows download page](https://www.postgresql.org/download/windows/).
It needs approximately 317 MiB of download plus extracted runtime/data storage.
It records the download URL/version/SHA-256 locally. This records downloaded
bytes; it does not claim an independently authenticated vendor checksum.

It creates a private runtime at `%LOCALAPPDATA%\FireWatch\PostgreSQL16`, outside
OneDrive. The data directory is persistent and listens only on `127.0.0.1`.
Authentication uses SCRAM-SHA-256. Windows ACLs restrict the runtime directory
and generated `.env` to the current user. Bootstrap superuser credentials stay
in the protected runtime; the application receives a separate non-superuser
`firewatch` role, owning `firewatch` and disposable `firewatch_test` databases.
Other database users do not receive public CONNECT privileges to these databases.

On first setup the helper creates `.env` with random database/operator secrets.
It never prints them or replaces an existing `.env`. `DATABASE_URL` is the sole
connection authority; `POSTGRES_*` documents provisioning. If using an existing
database, copy `.env.example` only when `.env` is absent, then configure a
dedicated role/database and replace required placeholders locally. Generated
bootstrap secrets must not be moved into Git or shared with teammates.

FIRMS credentials are optional for this bootstrap, which makes no provider calls.
Set a real MAP_KEY when satellite ingestion is implemented. An empty/placeholder
FIRMS key represents an unconfigured capability. Database password and a generated
operator token of at least 32 characters are required at API startup. Optional
chain secrets are required only when `CHAIN_ENABLED=true`. Enabling chain config
does not implement the future chain adapter.

## Run the API

```powershell
.\.venv\Scripts\python.exe -m uvicorn firewatch.main:app --host 127.0.0.1 --port 8000 --reload
```

In another PowerShell window:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
```

Expected response: `{"status":"ok"}`. Swagger is at `http://127.0.0.1:8000/docs`.
Liveness checks the API process only. It remains successful during a database
outage; use `firewatch db-check` separately. Invalid required configuration blocks
startup with field names and no secret values. Settings load once per process;
restart after changing `.env`. CORS uses explicit configured origins, without
browser credentials. Public/operator APIs and readiness arrive in later phases.

## Database lifecycle and backups

```powershell
.\.venv\Scripts\python.exe scripts/native_postgres.py status
.\.venv\Scripts\python.exe scripts/native_postgres.py stop
.\.venv\Scripts\python.exe scripts/native_postgres.py start
.\.venv\Scripts\python.exe scripts/native_postgres.py backup
```

The helper runs PostgreSQL as a background process owned by your user; no Windows
service or administrator account is needed. Run `start` after a Windows restart.
`stop` shuts down cleanly and preserves data. Repeating `setup` reuses the runtime,
role, databases and credentials. `pg_isready` verifies startup. Local custom-format
backups are under `%LOCALAPPDATA%\FireWatch\PostgreSQL16\backups`; these development
backups do not replace encrypted/off-server production backups or restore testing.
Never sync a running PostgreSQL data directory with OneDrive.

For an independently installed PostgreSQL Windows service, discover its actual
service name using `Get-Service *postgres*`, then use `Start-Service -Name
<actual-service-name>` from an appropriately privileged shell. Do not run both
installations on the same port or put administrator credentials in `.env`.

## Validation

```powershell
py -3.12 scripts/check_scaffold.py
.\.venv\Scripts\ruff.exe check src tests migrations scripts/native_postgres.py scripts/verify_bootstrap.py
.\.venv\Scripts\ruff.exe format --check src tests migrations scripts/native_postgres.py scripts/verify_bootstrap.py
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\python.exe -m pytest tests/unit
.\.venv\Scripts\python.exe -m pip check
```

Integration tests create/drop uniquely named tables. Use a disposable database,
not production. Load its DSN from local configuration without printing secrets:

```powershell
$env:FIREWATCH_TEST_DATABASE_URL = & .\.venv\Scripts\python.exe -c 'from firewatch.config import get_settings; from sqlalchemy.engine import make_url; print(make_url(get_settings().database_url.get_secret_value()).set(database="firewatch_test").render_as_string(hide_password=False))'
.\.venv\Scripts\python.exe -m pytest tests/integration
Remove-Item Env:FIREWATCH_TEST_DATABASE_URL
```

The DSN is captured into the environment, not displayed. Avoid shell transcripts
or echoing that variable. Linux CI verifies the same locks and real PostgreSQL 16
transactions on a GitHub-hosted service; no local container engine is involved.

## Maintaining dependency locks

```powershell
uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes --output-file requirements.lock
uv pip compile pyproject.toml --group dev --universal --python-version 3.12 --generate-hashes --output-file requirements-dev.lock
```

Review updates together and run Windows/Linux checks before promotion. Runtime
and development locks must use compatible shared versions. The build backend is
also pinned and installed from the development lock; `--no-build-isolation` avoids
silently resolving a separate build environment. Runtime-only deployments install
the hashed runtime lock followed by a trusted built wheel with `--no-deps`.

Phase 1.3 implements migrations through `0002`, typed schemas, idempotent region
seeding and completed-run publication. Apply migrations once before writers start;
the API never migrates automatically. See [database rules](database-schema.md).
Migration tests use generated schemas only in a disposable `_test` database.

A complete local acceptance check is `.\.venv\Scripts\python.exe scripts/verify_bootstrap.py`.
It checks the unprivileged application role, runs unit/integration tests against
`firewatch_test`, starts a temporary loopback API, verifies HTTP/OpenAPI/Swagger,
and stops that temporary API. The PostgreSQL server remains running.
