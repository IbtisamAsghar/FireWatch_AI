# Phase 2.2 acceptance: reusable FIRMS client

- [x] Fixed HTTPS host, allowlisted NOAA-20/21 NRT/SP products, bounded bbox and 1–5 day date inputs; redirects and future dates rejected.
- [x] Explicit connect/read timeouts, operation deadline, bounded exponential backoff/jitter and transient failure handling.
- [x] Numeric/date Retry-After honored; long rate limits are deferred, with persistent local quota reservations/cooldowns and provider usage preflight.
- [x] HTML and provider error bodies are rejected even with HTTP 200.
- [x] Valid header-only CSV returns an empty typed batch; malformed, unavailable and interrupted responses remain failures.
- [x] Raw entity bytes and sanitized hashed request/response manifests are saved before CSV inspection or future normalization.
- [x] Provider URLs and keys are redacted in standalone, CLI, third-party and exception logs; credential echoes are discarded.
- [x] Typed `FirmsBatch` and `SourceAssetInput` return retrieval, product, processing-version, raw-file, schema and checksum metadata.

The [client guide](firms-client.md) documents policy, quota estimation limits,
errors, evidence layout and `firewatch fetch-firms` usage. The production client
does not depend on the Phase 2.1 reconnaissance module.

Local verification on 21 October 2026:

- **292 tests passed**, including 62 new FIRMS tests, prior source/security tests,
  migrations, PostgreSQL integrity and API contracts. Test HTTP responses are
  invented, explicitly separated from the real smoke evidence.
- Real normal-API HTTP smoke passed, including readiness and operator authentication.
- The actual client fetched one NOAA-20 SP day (2022-09-07) for the northern
  California bbox: **323 raw rows**, saved unchanged with matching SHA-256 and
  sanitized manifest. See [smoke evidence](phase-2.2-smoke.json).
- Configured Ruff/format and strict mypy checks passed.

Normalization, deterministic observation keys and database upserts remain Phase
2.3; historical import remains Phase 2.4; scheduled overlapping refresh and source
status publication remain Phase 2.5. No new dependency, migration, database data,
region activation or hosted CI success is claimed.
