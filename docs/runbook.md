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


## Pipeline acceptance and handoff (Phase 3.7)

Run `python scripts/verify_pipeline.py` for real cached replay with dedicated-child
network isolation, abrupt termination/recovery and Parquet readback in a disposable
test schema. Run `python scripts/verify_bootstrap.py` for the full regression suite.
Export selected completed runs with `firewatch export-pipeline-handoff --run-id UUID
--output data/handoffs/new-package --data-kind real`; repeat --run-id as needed.
Use `firewatch verify-pipeline-handoff --path DIRECTORY --manifest-sha256 DIGEST`
before transfer/use. Preserve immutable handoff and replay directories separately.
See [Member 3 handoff](pipeline-handoff.md) for the reviewed local package receipt.

## Evidence audits (Phase 5.2)

Run `firewatch verify-alert --alert-id UUID --artifacts` for read-only payload and
local artifact checks. Retain the original raw/replay source and approved model
directories; missing files report unavailable or partial, never a false mismatch.
Investigate mismatches without changing immutable alerts or substituting a new
checksum. Restore the exact trusted files from backup to their configured roots,
then repeat verification. Files outside allowed roots are deliberately unavailable.
The CLI sends no chain requests; chain adapters and execution remain optional
later work. Use `--payload-copy FILE` only on a separate retained or synthetic copy
for tamper demonstrations. See [states, bounds and exit codes](evidence-verification.md).

## Local contract acceptance (Phase 5.3)

Run `python scripts/contract_tools.py install`, then `test` or `smoke`, from the
repository root. The smoke creates and stops a disposable loopback Anvil with a
fresh test-only signer. It does not configure application anchoring; receipt
addresses are historical test identities. No default Anvil key or valuable funds
are used. Follow [the registry guide](alert-registry.md) for wire encoding,
immutable-writer recovery limits and toolchain pins. Durable outbox processing,
RPC adapters and receipt/confirmation handling remain Phase 5.4.

## Durable signer and chain verification (Phase 5.4)

Run `firewatch anchor-worker` separately from the scoring worker after configuring
a registry and its dedicated signer. The API can omit the key. Use
`firewatch anchor-status --job-id UUID` for safe delivery details and
`retry-anchor --outbox-id UUID` after correcting eligible failures. Never reset
signed transaction metadata, skip a nonce or share the signer with external tools.
Confirmed anchors remain monitored for reorgs; outages do not alter saved evidence.
See [the complete operating guide](durable-anchoring.md) for spending/confirmation
policies, ambiguous broadcasts, nonce conflicts and recovery boundaries.
