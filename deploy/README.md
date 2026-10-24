# Deploy

The root Dockerfile supplies the non-root package image tested by Phase 5.5 CI.
It supports API, worker and Alembic command overrides and copies no secrets or
private data/model artifacts. See [CI and image checks](../docs/ci-and-integration.md).

Phase 5.6 adds `compose.yaml`, separate private Compose credentials, explicit
release migration/seeding, persistent volumes and optional Anvil/local Caddy.
Start with the [container stack runbook](../docs/container-stack.md). Runtime
Docker verification remains a hosted/local Docker-engine gate; configuration
validation alone does not prove service startup. Production HTTPS/deployment is
Phase 5.7. No public deployment exists yet.
