# Phase 5.6 acceptance: local full-stack backend

- [x] One digest-pinned Linux image, hashed runtime lock, installed wheel and
  UID/GID 10001 application processes; explicit API/worker/migration commands.
- [x] Compose DB/API/worker, one-shot storage/migration/seed/operator tools,
  optional internal Anvil/signer and optional local HTTP Caddy profile.
- [x] Separate protected `.env.compose` generation, no overwrite/read of native
  `.env`; bootstrap DB credentials isolated from application containers.
- [x] Unprivileged application role, DB health ordering, bounded schema-aware
  startup retry, existing runtime outage recovery and graceful signal handling.
- [x] Persistent database/raw/model/replay/Anvil/Caddy volumes; API read-only
  artifact mounts and worker write access limited to raw processing state.
- [x] Health checks, restart policies, bounded stdout/stderr log retention and
  documented startup/stop/recovery commands without a frontend process.
- [x] Private versioned managed-model transfer verifies trusted identities and
  bytes atomically; no model approval/activation or training archives in image.
- [x] Actual Docker acceptance script and hosted CI gate cover first boot,
  role/mount isolation, container recreation persistence and optional services.
- [ ] Successful Docker-engine execution on this Windows host: Docker is not
  installed; Compose configuration validation does not establish runtime success.

See [the stack runbook](container-stack.md) and [local validation receipt](phase-5.6-smoke.json).
Hosted execution is not claimed. Local Python/PostgreSQL results and exact checks
are recorded in the receipt. The complete offline PostgreSQL regression passed
**1,260 tests**, with one existing Windows real-symlink skip, in **475.20 seconds**.
The focused deployment suite passed **17 tests**. Strict mypy passes 106 source
files; Ruff/format, actionlint, all-profile Compose validation, hashed locks,
source/wheel builds and installed-wheel entrypoint/startup-gate checks pass.
Gitleaks reports zero Git-history/worktree findings. An offline-fixture ownership
fix ensures test-specific DB mocks are restored before schema cleanup.

Deployment and HTTPS remain 5.7;
off-machine backups/monitoring remain 5.8. No public service, real model approval
or application database/configuration change is performed by phase acceptance.
