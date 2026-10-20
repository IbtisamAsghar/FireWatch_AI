# Phase 1.2 acceptance record

Scope: installable backend bootstrap and native Windows PostgreSQL, following the
user's decision to avoid Docker Desktop. Application schema/migrations, provider
ingestion, worker execution and ML remain in their later phases.

- [x] Installable `src/firewatch` package and `firewatch` console command.
- [x] Runtime dependencies and development dependency group in `pyproject.toml`.
- [x] Exact transitive runtime/development locks with package SHA-256 hashes.
- [x] Python 3.12 Windows environment installed and dependency compatibility checked.
- [x] Settings validate required DB/operator secrets and conditional source/chain inputs.
- [x] API startup errors omit credential values; CLI help works without secrets.
- [x] Native PostgreSQL 16.15 binaries installed outside OneDrive, with persistent data.
- [x] Loopback-only listener, SCRAM authentication, protected local credentials.
- [x] Dedicated non-superuser application role and development/test databases.
- [x] Pool bounds, pre-ping, connection/statement timeouts and UTC sessions.
- [x] Request sessions close without implicit commit; write contexts commit/rollback.
- [x] `firewatch --help`, `--version`, `check-config` and `db-check` implemented.
- [x] Typed `/health/live` response, OpenAPI and Swagger bootstrap.
- [x] Native lifecycle/backup helper and reproducible PowerShell setup instructions.
- [x] Linux PostgreSQL CI and Windows Python-package CI configured.
- [x] Final local database/restart/HTTP acceptance checks completed.
- [ ] Linux hosted runtime/dev lock, build and real PostgreSQL verification passed.
- [ ] Windows hosted installation and unit tests passed.

## Verification record

Initial local checks: 22 unit tests passed; Ruff, strict mypy and pip compatibility
checks passed. Editable install, CLI and source/wheel builds succeeded. Native
PostgreSQL reported readiness on 127.0.0.1:5432 and generated local secrets without
printing them. Final integration/restart/HTTP and hosted results are recorded below
when completed. A Windows inherited-output-handle startup issue was corrected by
using output sinks for background server startup commands.

## Design boundaries

Database and operator credentials are required now; FIRMS access is optional
until ingestion exists. Provider registration does not block bootstrap. A real
MAP_KEY must be supplied for the later ingestion phase. Chain settings validation
does not implement an adapter. No paid service was selected.

The native database runs as a per-user background process, not a machine-wide
Windows service. Start it after reboot with the documented helper. Existing native
service installations can use the documented service lifecycle instead. Keep local
data/backups out of OneDrive. Production off-server backups and restore procedures
remain Module 5 work.

Phase 1.1's independent-review requirement remains in effect. Phase 1.2 may be
developed on top of its setup branch at the user's request; merge the prerequisite
setup PR before merging the bootstrap PR. No shared-main bypass is required.

Local final acceptance: 26 tests passed, including real PostgreSQL transactions,
rollback on failure, no accidental request-scope commit, UTC and PostgreSQL 16
checks. The non-administrative app role was verified. Native stop/start and
repeatable setup passed; the existing .env was preserved. A custom-format local
backup was created. Real Uvicorn HTTP checks passed for liveness, Swagger and
OpenAPI, and the temporary API process was stopped. PostgreSQL remains running.
Runtime/development locks contain 35/52 packages with matching shared versions.
No provider API key or paid service was needed. Existing source modules beyond
the bootstrap remain explicit placeholders, not implemented pipeline behavior.

Final distribution check: the rebuilt wheel installed into a fresh runtime-only
virtual environment using the hashed runtime lock; pip reported no broken
requirements, version was 0.1.0 and PostgreSQL db-check succeeded. PowerShell
bootstrap syntax was checked. Ruff/format and strict mypy passed on the final
source. No real local credentials were included in the staged source files.

Publication status: automatic approval review rejected pushing this branch and
opening a PR because external publication for this phase needs explicit approval.
All changes remain on local `codex/phase-1-2-bootstrap`; hosted Linux and Windows
acceptance is pending. The prepared CI workflow performs those checks after
publication. No Phase 1.2 PR or hosted CI success is claimed.
