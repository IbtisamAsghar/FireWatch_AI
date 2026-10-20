# Phase 1.3 implementation and acceptance

Branch: existing `development`; no new branch or PR is required for this phase.
Native PostgreSQL continues without Docker Desktop.

- [x] Shared table contracts implemented as 19 SQLAlchemy tables, including
  observation/source/region lineage and the latest-live publication pointer.
- [x] Frozen Alembic revisions 0001 (tables/constraints/indexes) and 0002 (guards).
- [x] Versioned typed DTOs for satellite, weather, climate, snapshots, features,
  scoring, evidence and run handoffs, ready for Members 2-5 to review.
- [ ] Human agreement on the DTO contracts by Members 2-5 recorded. No team
  approval is fabricated; integration must use/review the documented contracts.
- [x] Coordinates, sensor/products, timestamps, enums, numeric ranges and schema
  versions validated; raw conversion failures remain available with nulls/flags.
- [x] Database uniqueness, composite references, provenance, indexes and exact
  evidence digest checks; saved alert UPDATE/DELETE/TRUNCATE blocked.
- [x] Empty upgrade, populated previous revision upgrade, downgrade/re-upgrade
  and metadata drift tested against isolated native PostgreSQL schemas.
- [x] Validated region seeding is atomic, repeatable and preserves UUIDs.
- [x] Completion/publication share a transaction; unfinished/replay/older runs
  cannot replace latest live. Direct SQL, concurrent publishers and failure
  rollback are covered.

## Local verification

82 tests passed, including the existing bootstrap suite and new migration,
DTO, provenance, raw-SQL integrity and concurrent-publication checks. Ruff,
formatting and strict mypy passed before application migration. The real API
bootstrap smoke test still passed for liveness, Swagger and OpenAPI.

A native development backup was taken before applying migrations. Application
database migration reached `0002 (head)` and `alembic check` reported no new
upgrade operations. Running the seed CLI twice returned the same UUID for the
single disabled demo region. No real FIRMS credential or provider calls were
needed. There are no fabricated observations, model results or alerts in the
development database; synthetic integration records live in disposable test
schemas which are removed after tests.

`contracts-v1` and field meanings are documented in [contracts](contracts.md),
[schema rules](database-schema.md), and the [data dictionary](data-dictionary.md).
Pipeline calculations, business API routes, worker scheduling, model training,
evidence canonicalization and chain transactions remain their later phases.

## Operational boundaries

Migrations never run inside API startup. Run them once as an operator before
writers. Run publication repositories inside caller-owned transactions. The
source adapters must still implement actual conversion, source access, unit
validation and revision selection. The disabled demo bbox is an illustrative
configuration, not a source-coverage or event-quality assertion.

Human contract review remains an external coordination task. Hosted CI is
configured for `development`, including migration tests on PostgreSQL 16;
the hosted implementation run has now passed all four checks (record below).


## Hosted verification

Implementation commit `ea47dd6` passed `backend-linux`, `backend-windows`,
`scaffold`, and `scaffold-windows`:
https://github.com/IbtisamAsghar/FireWatch_AI/actions/runs/39460725845

Linux CI exercised real PostgreSQL migrations/integrity and all 82 tests,
Ruff/format, strict mypy, hashed development/runtime installations, pip
compatibility, CLI and distribution builds. Windows CI verified hashed dependency
installation and unit tests. The implementation is published on the existing
development branch; no new branch or PR was created. This record update changes
documentation only. Team DTO agreement remains a human review item.
