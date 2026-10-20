# Contracts

Status: Phase 1.2 bootstrap implemented; business APIs remain design contracts.

`GET /health/live` returns HTTP 200 and `{"status":"ok"}` without querying
PostgreSQL or external providers. Configuration validates at startup. Readiness,
regional reads, jobs and operator authentication endpoints arrive in later phases.
`firewatch check-config` reports sanitized configuration status; `firewatch db-check`
checks a pooled UTC PostgreSQL session. Invalid configuration exits 2 and database
unavailability exits 1 without exposing driver details or credentials.

Member 1 will version Pydantic DTOs, internal service interfaces, REST schemas, error envelopes, operator auth, pagination, idempotency and completed-run publication. Use implementation-plan sections 5, 6 and 13. Keep public and operator responses separate. Bootstrap OpenAPI is available at `/openapi.json` when the API is running.
