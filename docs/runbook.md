# Operations runbook

Status: deployment not implemented.

Member 5 will supply exact local/server commands, migration and release order, persistent storage, source/worker monitoring, retry/recovery procedures, encrypted backup/restore, and application/model rollback. Never publish database/chain ports or secrets. Validate a restore and restart before declaring delivery complete. Local tools can be checked using the README; native Windows PostgreSQL and Python services begin in Phase 1.2. Docker Desktop is not required locally. Linux Docker Engine/Compose is a separate future production option.
