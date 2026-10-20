# Database schema and migration ownership

Schema revision: `0002`. Member 1 owns migration numbering and integrates shared
model changes. PostgreSQL 16 is required; there is no SQLite path, PostGIS
requirement, or extension dependency. Python models live in
`src/firewatch/db/models.py`; value enums and DTOs live in `src/firewatch/schemas`.

## Run migrations and seed

From the repository root with the configured native PostgreSQL server running:

```powershell
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe current
.\.venv\Scripts\alembic.exe check
.\.venv\Scripts\firewatch.exe seed-regions --config configs/regions.yaml
```

The first frozen revision, `0001`, creates 19 application tables, indexes,
foreign keys and constraints. Revision `0002` installs PostgreSQL integrity
functions/triggers. Migration revisions contain frozen schema definitions;
they do not call the current ORM's `metadata.create_all`. Online credentials come
from validated local settings, never from `alembic.ini`. `alembic upgrade head
--sql` emits credential-free SQL without a database connection. Run one migration
process at a time before starting writers. The API never migrates at startup.

Seeding validates the whole YAML configuration before entering a transaction.
Names are unique slugs. Repeated seeds retain UUIDs and update configured fields;
omitted regions are preserved. The supplied `demo-region` uses an illustrative
northwest Pakistan box and stays **disabled** until Member 2 verifies source
coverage. This does not claim any specific fires or usable training events.

## Tables and reference boundaries

- `regions`: unique name, ordered longitude/latitude bounding box, enabled flag
  and description. Antimeridian-spanning boxes are outside the regional MVP.
- `source_assets`: provider/product, sanitized request metadata, retrieval time,
  storage path, format/processing version and SHA-256 digest. A digest is not
  globally unique: independent retrievals may share bytes and retain provenance.
- `ingestion_batches`: region, satellite product, ordered UTC window, source
  asset, job status, quality/duplicate counts and sanitized failure reason.
- `detections`: globally unique versioned dedup key, initial batch reference,
  product/sensor, coordinates, acquisition time, nullable physical values,
  raw confidence, normalized confidence, raw fields and quality flags.
- `detection_sources`: unique observation/batch association and the input record
  as JSON. Multiple downloads/revisions retain separate source lineage.
- `detection_regions`: unique region/observation association with an acquisition
  time validated by a composite FK; indexed by region and acquisition time.
- `weather_records`: source/model/product/version, requested/returned coordinates,
  cell, valid/retrieval times, nullable weather values, units/flags and asset.
  The source/model/product/cell/time/version tuple is unique.
- `climate_baselines`: cell/source, fixed ordered reference years, month,
  statistic definitions, nullable means, sample counts, version and asset.
  Reference identity is unique across the relevant source/cell/month/version.
- `runs`: region/mode/cutoff, deterministic run key, status, configuration hash,
  input manifest, stage checkpoints, coverage, scorer/feature versions and times.
- `live_run_heads`: at most one completed live run per region. A composite FK
  ties the region to the exact run. Replay has no corresponding publication head.
- `jobs`: operator task type, bounded service-owned payload, unique idempotency
  key, payload hash, status, attempts, availability/lease/heartbeat, result and error.
- `cluster_snapshots`: run-specific centroid, positive member count, nullable
  FRP aggregates, latest detection and algorithm version.
- `cluster_members`: unique snapshot/observation pairs. Observations must belong
  to the run region and cannot occur after the feature cutoff.
- `feature_sets`: one feature vector per snapshot, run/feature version, values,
  missing flags, units, lineage and hash. Composite FK prevents cross-run mixing.
- `model_versions`: unique version, trusted artifact path/hash, feature version,
  training manifest, metrics, target, lifecycle status and probability-support flag.
- `scores`: snapshot/run/scorer, optional matching model, ranking value, score
  kind, nullable probability, priority, explanation and scoring time. Snapshot/run
  and model/scorer references are composite FKs. Ranking lookup is indexed.
- `alerts`: one evidence payload per score, exact canonical bytes, digest,
  evidence version and creation time. Composite FK binds score/snapshot/run.
- `anchor_outbox`: unique alert/network, retry state, availability, transaction
  and receipt metadata. It remains mutable independently of saved alert bytes.
- `anchors`: alert/chain/contract identity, transaction/block metadata, digest,
  network kind and confirmation status. This stores future anchoring results;
  Phase 1.3 sends no transactions and creates no chain connection.

Foreign keys use restrictive deletion; there are no application-data cascade
deletes. Enums are named VARCHAR check constraints, avoiding shared native-enum
type lifecycle conflicts during migrations. UUIDs have application and database
defaults. All persisted timestamps use `timestamptz`.

## Publication transaction

Call `complete_and_publish_run(session, run_id)` inside `Database.transaction()`.
It locks the region and run, flushes pending checkpoints/outputs, and refreshes
the run before completion. It does not commit independently.

Every required stage (`ingest`, `snapshots`, `enrichment`, `features`, `scoring`,
`evidence`) must succeed. Coverage must be complete or explicitly partial.
Snapshots need exact member counts/latest observation times, matching features
and the selected scorer. Critical scores need saved evidence. Empty completed
runs are valid when all stages succeeded with usable coverage.

Completion and the latest-live pointer commit together. A failed transaction
preserves the previous pointer and running state. Replay may complete without
changing live publication. An older live run may complete but cannot replace a
newer head; equal-cutoff ties preserve the existing head. Repeating publication
of the same completed run is idempotent. PostgreSQL guards apply these rules to
ordinary direct SQL as well as ORM/repository calls.

Completed runs and their snapshots/members/features/scores are immutable to
ordinary INSERT/UPDATE/DELETE operations; truncation is blocked when completed
run data exists. New incoming observations may be saved separately. Baseline
and uncalibrated model scores cannot expose probabilities. Supervised scores
must reference matching model/scorer and feature versions; probability scores
also require a release explicitly marked as supporting probabilities. That flag
records an operator decision, not evidence of scientific validation by itself.

## Evidence boundaries

Alert insertion checks SHA-256 against exact nonempty BYTEA payload bytes.
UPDATE, DELETE and TRUNCATE are blocked for saved alerts. Anchoring status is
kept in separate tables so retries never modify original evidence. Canonical
payload construction, source artifact verification and blockchain adapters are
Module 5 work. A privileged owner can alter database schema or disable triggers;
these guards do not replace independent evidence anchoring or production role
separation. Integrity checks do not establish fire reality or model quality.

## Reviewing changes and rollback

Use `alembic revision --autogenerate -m descriptive_name`, inspect every generated
operation, add custom SQL explicitly, and run migration/integrity tests. Alembic
autogeneration does not manage trigger-function changes; version those manually.
`alembic check` detects table/model drift, while integration tests exercise the
custom triggers. Keep committed revisions immutable after release.

Downgrades remove guards (`0002 -> 0001`) or tables (`0001 -> base`). These are
tested only in disposable schemas. Never downgrade a real database as a routine
validation step. Back up before production changes and follow the later runbook's
restore/forward-fix procedure for destructive changes.

Tests require `FIREWATCH_TEST_DATABASE_URL` targeting a database ending `_test`.
They create/drop only generated `fw_test_<UUID>` schemas, including upgrade from
empty, upgrade from a populated `0001`, downgrade/re-upgrade, metadata drift,
concurrent publishers, and direct-SQL bypass attempts. See
[local validation commands](local-development.md).
