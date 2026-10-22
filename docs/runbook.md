# Operations runbook

Status: deployment not implemented.

Member 5 will supply exact local/server commands, migration and release order, persistent storage, source/worker monitoring, retry/recovery procedures, encrypted backup/restore, and application/model rollback. Never publish database/chain ports or secrets. Validate a restore and restart before declaring delivery complete. Local tools can be checked using the README; native Windows PostgreSQL and Python services begin in Phase 1.2. Docker Desktop is not required locally. Linux Docker Engine/Compose is a separate future production option.

Native local lifecycle and development backup commands are now implemented in
[the local development guide](local-development.md). PostgreSQL runs per user
without a machine-wide Windows service; start it manually after reboot. Production
deployment, encrypted external backups and restore/rollback acceptance remain pending.

Phase 1.3 migration, seed and publication guidance is now implemented in
[database operations](database-schema.md). Back up before upgrades. Revision
0002 protects saved evidence and completed outputs; tests downgrade only their
own disposable schemas. Production role separation and restore drills remain Module 5.


## Local worker (Phase 3.5)

Start the existing PostgreSQL cluster with `python scripts/native_postgres.py start`.
Run `python -m firewatch.worker` separately from Uvicorn, or use `--once --no-schedule`
to process one admitted job. Stop with Ctrl+C. Inspect authenticated job status;
expired leases are reclaimed automatically, committed stages are retained, and
failed refreshes keep the previous live head. See [worker operating guide](durable-worker.md)
for queue bounds, retry codes, source policies and crash acceptance commands.
Production service installation and monitoring remain delivery work.


## Offline historical replay (Phase 3.6)

Preserve `data/replays/<scenario-id>/` with `configs/replays.yaml`. On a migrated
PostgreSQL database, run `firewatch replay --scenario norcal-active-v1`,
`norcal-quiet-v1` or `norcal-degraded-v1`. The CLI verifies/imports bundles and
prints the completed replay run UUID. Regions may remain disabled; live heads are
never advanced. Repeated admission reuses completed outputs. For a missing or
tampered bundle, restore the exact reviewed directory/catalog pair before retrying;
do not replace its checksum to bypass verification. Existing UUID/data conflicts
require an isolated fresh database or an explicit data investigation. See
[replay operating guide](deterministic-replay.md).
