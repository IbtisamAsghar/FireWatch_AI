# Shared contracts: contracts-v1

Phase 1.3 implements these typed handoffs in `src/firewatch/schemas/contracts.py`
and shared enums/value types in `schemas/common.py`. They are ready for review by
Members 2-5 before integration; no human sign-off is implied by implementation.

## Cross-module rules

DTOs forbid unknown fields, invalid enum values and non-finite numbers. Timestamp
inputs require an explicit timezone; offset-aware values normalize to UTC and
serialize with `Z`. Numeric epochs and naive timestamps are rejected. Coordinates
are latitude/longitude fields; future GeoJSON uses longitude first. Null denotes
missing values; it is never silently converted to zero. Versions are nonempty
strings, digests are lowercase 64-character SHA-256 hex, and identifiers are UUIDs.

`contracts-v1` is an enforced literal, not an arbitrary client-provided label.
Run-bound processing/scoring outputs carry run ID, mode, cutoff, schema and
feature versions. Source records created before a run use their own asset/batch
lineage. Phase 1.4 implements REST serializers in `schemas/api.py` as `api-v1`;
see [frontend handoff](frontend-handoff.md) for endpoint, filter and error contracts.

## Member 2: satellite and source inputs

- `BoundingBox` validates global bounds and ordered west/east, south/north.
- `RegionDefinition` and `RegionConfig` validate unique names and regions-v1 YAML.
- `SourceAssetInput` describes provider, product, sanitized request metadata,
  retrieval, path, content digest, format and processing version. Common credential
  keys and unredacted FIRMS credential paths are rejected. Clients must sanitize
  metadata before constructing it; never log raw validation input or full URLs.
- `DetectionInput` validates NOAA-20/NOAA-21 NRT/SP products and sensor agreement,
  coordinates, acquisition time, nonnegative FRP/scan/track, positive brightness,
  confidence/day-night enums and deterministic dedup key. Invalid converted
  values go in `raw_values`, with the normalized value null and explicit flags.
  The future normalizer owns conversion decisions and dedup-key construction.
- `DetectionBatch` checks product consistency and the ordered UTC import window.

`save_detection(session, batch_id, region_id, observation)` is the shared
persistence boundary. It validates the batch/region/product/time/bbox, inserts
once by dedup key, and preserves every distinct batch association and region.
An identical key with conflicting observation identity is rejected. Different
normalized values for the same identity do not overwrite the first stored
observation; their full input is retained in source lineage. Member 2 must define
the explicit NRT/science revision-selection policy before live/archive ingestion.

## Member 3: enrichment, snapshots, features and runs

- `WeatherRecordInput` and `WeatherSeries` preserve cell/source/model/product,
  requested/returned coordinates, UTC valid/retrieval times, units and flags.
  Humidity is 0-100%, temperature cannot fall below absolute zero, wind/gust/rain
  cannot be negative. Missing values remain nullable. Provider adapters must
  validate returned unit metadata and precipitation intervals in Module 3.
- `ClimateBaseline` validates reference-year order, month, sample counts,
  source/version and nullable means. The feature builder must ensure the
  reference ends before the snapshot/evaluation period.
- `ClusterSnapshot` carries run context and unique member IDs; latest observation
  cannot follow the cutoff. It describes a run-specific cluster, not a tracked fire.
- `FeatureSet` carries values, missing indicators, unit map, lineage and hash.
- `RunResult` describes status/coverage/stages. A completed result requires all
  stage checkpoints, usable coverage and a completion time at/after the cutoff.

Use `complete_and_publish_run()` inside a caller-owned transaction after successful
stages. Database checks additionally verify persisted members/features/scores and
critical evidence. See [publication and concurrency rules](database-schema.md).
The worker, clustering and feature calculations themselves remain Module 3 work.

## Member 4: scoring

`ScoreResult` carries run/snapshot, scorer and optional model, rank value, kind,
priority, nullable probability, reasons, structured explanation, flags and time.
Baseline scores have no model ID; supervised scores require one. Only the
`probability` kind can contain a probability, and it must be between zero and one.
Scoring cannot precede the feature cutoff. Ranking values are nonnegative and
version-specific; they are not interchangeable across scorers or probabilities.

The database registry binds model version and feature version. A probability
claim also requires `model_versions.probability_supported=true`. Model training,
calibration evidence, promotion and SHAP remain Module 4 work; no model was
trained or promoted by the schema phase.

## Member 5: evidence and delivery

`AlertEvidence` binds alert/run/snapshot/score, exact payload bytes, digest,
evidence/feature versions and times. DTO and database both check that the digest
matches the nonempty bytes. SQL triggers block later evidence modification.
Canonicalization and payload field/precision rules remain Module 5 work.
Outbox/anchor records are separate mutable delivery state; no chain call is made
in this phase. Integrity does not prove incident reality or scientific accuracy.

## Current runnable API and CLI

`GET /health/live` returns HTTP 200 and `{"status":"ok"}` without querying
PostgreSQL or providers. Settings validate at startup. Bootstrap OpenAPI is
available at `/openapi.json`; regional reads/readiness/jobs/auth endpoints follow
in Phases 1.4/1.5.

`firewatch check-config` emits sanitized status, `db-check` checks a pooled UTC
connection, and `seed-regions --config configs/regions.yaml` creates/updates regions
atomically with stable IDs. Invalid configuration exits 2; database failure exits
1 without exposing credentials or driver details. CLI help needs no secrets.

## Phase 1.4 producer metadata and public projections

Completed outputs remain immutable. Before completion, a pipeline may save
`input_asset_manifest.freshness.latest_weather_valid_at` and
`latest_retrieved_at` as timezone-aware ISO strings for run-specific freshness.
Missing metadata remains null, never substituted from unrelated weather rows.
Replay scenarios expose completed runs only when the saved
`replay_scenario` object contains a slug `id` and optional safe `name` and
`description`. The API does not accept arbitrary replay imports or create runs.

Shared read selection joins snapshots, the run's selected scorer, matching
features and optional alert. Lists, GeoJSON and summary use the same predicates.
Read sessions issue `SET TRANSACTION READ ONLY`; no GET publishes a run or job.
Evidence API payloads are credential-safe projections, while SHA-256 verification
always uses the immutable original canonical bytes. Source-file/artifact verification
is explicitly not performed by the alert verification endpoint.
