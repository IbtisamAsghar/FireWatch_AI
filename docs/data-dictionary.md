# Data dictionary: schema revision 0003

The authoritative field/type/constraint definitions are the SQLAlchemy models in
`src/firewatch/db/models.py` and typed DTOs in `src/firewatch/schemas/contracts.py`.
[Database documentation](database-schema.md) explains the table references,
uniqueness, indexes, integrity triggers and migration lifecycle.

## Common values

- `id`, region/run/snapshot/model/score/alert IDs: UUID; IDs do not identify a
  persistent physical fire across snapshot dates.
- `acquired_at`: satellite acquisition instant; `valid_at`: weather valid time;
  `retrieved_at`: asset download time. These are different concepts.
- `as_of`: feature cutoff; `started_at`, `completed_at`, `created_at`, `scored_at`:
  actual processing/storage times. All use PostgreSQL timestamptz and UTC sessions.
- `latitude`, `longitude`: decimal degrees, -90..90 / -180..180. Regions use
  west/south/east/north and require strict coordinate ordering.
- `mode`: live or replay. Run-bound records link to one immutable completed run;
  replay never updates a live publication head.
- `content_hash`, `config_hash`, `feature_hash`, `artifact_hash`, `original_hash`,
  `payload_hash`, `anchored_digest`: lowercase SHA-256 hex digests with separately
  defined/versioned content. A hash name does not imply the bytes were independently
  authenticated. Canonical evidence hashing additionally checks exact stored bytes.
- JSONB metadata has schema/processing versions in its surrounding record. Never
  derive evidence bytes from PostgreSQL's JSON formatting.

## Satellite observations

Allowed products are VIIRS_NOAA20_NRT, VIIRS_NOAA21_NRT, VIIRS_NOAA20_SP and
VIIRS_NOAA21_SP; corresponding sensors are NOAA-20 and NOAA-21.

`frp_mw` is fire radiative power in MW, nonnegative when known. `brightness_k` is
reported I-4 brightness temperature in Kelvin, positive when known. `scan` and
`track` preserve nonnegative provider dimensions; Member 2 records exact source
units/precision in normalization documentation. `daynight` is D or N, nullable.

`confidence_raw` preserves the provider class/text; normalized `confidence` is
low, nominal, high or unknown. No confidence percentage is fabricated.
`raw_values` and `quality_flags` preserve conversion failures with null normalized
values. Null FRP does not mean zero activity. Row rejection policies are a
normalizer responsibility, not automatic imputation by the schema.

One `dedup_key` identifies a versioned normalized observation independently of the
requesting region. `primary_batch_id` preserves initial lineage, with additional
batch/raw-record associations in `detection_sources`. `detection_regions` supports
overlapping region membership without duplicating the observation. It repeats
acquisition time only for the region/time index; a composite FK enforces agreement.

## Weather and climate

`temperature_c` / `temperature_mean_c`: Celsius; `humidity_pct`: percent;
`wind_ms` / `gust_ms`: m/s; `precipitation_mm` / `precipitation_mean_mm`: mm.
Wind, gusts and rain are nonnegative. `units` preserves explicit provider unit
metadata; adapters must validate it before ingestion. `model`, `product`, cell
and source version distinguish forecast archives from live forecasts/reanalysis.
Requested coordinates and returned weather-grid coordinates are both retained.

Climate `reference_start_year`/`reference_end_year` are inclusive; month is 1-12.
`statistics` records definitions and temporal convention; `sample_counts` records
coverage. Monthly daily-reference climate means are context, not standardized
hourly anomalies. Fixed reference selection and rolling rainfall completeness
are Module 3 implementation requirements. Missing baselines remain null/flagged.

## Runs, features and scores

Run status is queued/running/completed/failed. Coverage is complete/partial/
unavailable. Completion requires all six named stages succeeded and usable
coverage; a successful empty run remains distinct from unavailable source data.
`run_key` deduplicates execution identity; `config_hash` records configuration.
Jobs separately store queued/running/succeeded/failed and idempotent request hashes.

Snapshot FRP aggregates remain nullable when unavailable. Positive `member_count`
must match saved membership at completion, and latest acquisition must match its
members. Feature values, missing indicators, unit map, lineage, feature version
and hash are saved separately. Features/scores cannot reference another run's
snapshot. Future observations cannot become snapshot members.

Phase 3.3 diagnostics in `runs.input_asset_manifest.clustering` include sensor and
confidence category counts, centroid-near 6/12/24-hour counts, valid/missing FRP
member counts and maximum centroid distance. FRP aggregates use known values;
partial aggregates are flagged and all-missing aggregates are null. Centroid
activity counts need not equal member count because DBSCAN can chain beyond the
centroid radius. Snapshot IDs are run-specific and are not incident identities.
See [cluster definitions and limitations](cluster-snapshots.md).

Phase 3.4 fixes 17 model values and 14 boolean missing indicators. Model values
exclude coordinates, UUIDs, timestamps, labels and score/status metadata. Counts
refer to centroid-near satellite observations, first-seen age is bounded to the
preceding 24-hour lookup, and unknown day/night never counts as nighttime.
`confidence_high_fraction` excludes unknown categories; it is not a probability.
Weather values use the last completed UTC hour, precipitation requires all 24
preceding hourly sums, and climate departures use the fixed daily reference.
`feature_hash` binds the canonical FeatureSet payload excluding itself, including
context, units and lineage. See [full definitions](features-v1.md) and the
[machine-readable input schema](features-v1.schema.json).

Score kinds are baseline/model_score/probability; priorities are LOW/MEDIUM/HIGH/
CRITICAL. `priority_score` is a finite nonnegative ranking index on its version's
scale. `escalation_probability` is null for baselines/unvalidated scores and 0-1
only for a model release explicitly supporting that claim. Model/scorer/feature
references must agree. `explanation` stores the future scorer's actual factors;
this schema phase produces no measured predictions or scientific explanations.

## Evidence and optional chain

`canonical_payload` is the original BYTEA evidence, with SHA-256 `original_hash`,
evidence version and actual creation time. One score has at most one immutable
alert. Alerts bind the exact score/snapshot/run tuple. Critical selected scores
require evidence before publication. Byte formatting and full source manifests
are Module 5 responsibilities.

Outbox status is queued/submitted/confirmed/failed. Anchors store a positive chain
ID, EVM contract address, transaction/block hashes, nonnegative block height,
digest, confirmation status and local/testnet/mainnet network kind. Local chain
identity does not imply independent notarization. Anchor failures do not mutate
evidence or block backend results. No blockchain service is required for seeding
or migration.

## api-v1 read responses

The generated OpenAPI schema is the exact response field dictionary.
Run-bound responses contain `run_id`, `mode`, `as_of`, `data_completeness`,
`freshness`, `schema_version=api-v1` and the required research notice.
Freshness includes evaluation/reference UTC times, observation/weather/retrieval/
scoring/completion times, age in hours, nullable stale flags and quality flags.
Historical age uses the replay cutoff. A null age or stale flag means unknown.

Snapshot coordinates are degrees; GeoJSON orders longitude before latitude.
`frp_max_mw`/`frp_sum_mw` use MW; feature detail carries explicit units and
missing flags. Scores remain ranking values; only `score_kind=probability`
supports a non-null escalation probability. Page totals precede pagination;
GeoJSON includes the bounded return count and explicit truncation boolean.
Source status uses retrieval age and, when detections exist, acquisition age;
an empty valid fetch can be fresh. DETECTION_MAX_AGE_HOURS defaults to 24.
Alert hashes identify original canonical bytes, not their public payload projection.
See [frontend handoff](frontend-handoff.md) for enums, errors and examples.

## Operator job admission

`JobAccepted` carries public `schema_version=api-v1`, research notice, UUID job ID,
job type/status, availability UTC time and nullable result run ID. Location identifies
the authenticated status resource; Idempotency-Replayed identifies retries.
Worker payloads are private `jobs-v1` objects. Refresh/replay freeze region, mode,
window, cutoff and versions; replay adds scenario metadata and imported asset UUIDs;
anchor adds outbox/alert IDs, configured chain/contract, network identity and original
evidence digest. No key, token, RPC URL or source download URL is accepted/stored.
The jobs `payload_hash` is the canonical request identity digest for idempotency,
not an integrity signature or checksum of the enriched worker payload.

## Committed public field dictionary

[api-field-dictionary.json](api-field-dictionary.json) contains every OpenAPI
request/response/enum component, required field, nullable type, constraint and
description, with coordinate/time/unit notes. Local component references retain
their OpenAPI paths under `components`. A pure contract test rejects drift between
this committed snapshot and the generated schema on Linux and Windows.

After an intentional API change, regenerate with
`python scripts/export_openapi.py --field-dictionary docs/api-field-dictionary.json`,
review and commit the dictionary with the source change. The frontend bundle uses
the same generator and also includes human-readable contracts and examples.


## Phase 3.5 durable execution

Job attempts are monotonic lease fencing tokens. Queued retries retain attempts,
available_at, fixed safe error code, result_run_id and completed checkpoints.
Worker payload_hash remains an admission identity hash. Run config_hash covers the
frozen orchestration request/config/bbox/evidence policy. coverage_warnings are
separate from stage_status; freshness uses actual pinned retrieval and valid times.
See [worker contracts and baseline thresholds](durable-worker.md).


## Phase 3.6 replay bundles

`bundle_sha256` identifies exact canonical replay-bundle-v1 manifest bytes. File
inventory entries contain relative path, byte size and SHA-256. data_kind separates
real observations from synthetic fixtures; scenario_kind is active/quiet/degraded.
Canonical input UUIDs, raw source hashes/retrieval times, explicit satellite policy,
EnrichmentPins and feature configuration are retained. as_of is the historical
cutoff; processed_at/scored_at record actual later execution. See
[replay catalog](replay-catalog.json) and [definitions](deterministic-replay.md).


## Phase 3.7 Parquet/status handoff

feature-handoff-v1 stores the 17 features-v1 values plus 14 missing indicators and
13 metadata columns. Nullable floats stay null; counts are int64 and missing flags
are boolean. as_of/latest_detection_at/processed_at are UTC microsecond timestamps.
Membership rows bind run/snapshot/detection to source batch/revision and acquired_at.
Run and job snapshots retain api-v1 DTOs. The manifest includes checksums, declared
data kind, package/Python/Arrow versions, run IDs and row counts. labels_included
and training_ready are false. See [schema](pipeline-handoff.schema.json).

## Phase 4.6 saved scoring explanations

Baseline explanation `contributions` contain actual FRP MW or weighted index-point
terms. Supervised explanation `contributions` contain 31 feature entries with signed
raw forest-vote contribution, observed value, imputed state and training median
where used. `base_value` plus all contributions equals `raw_model_score`, not the
calibrated probability. The method, background, feature order, threshold metadata,
feature hash, inference digest and approval reference are explicit. `warnings`
inside explanation JSON survive existing score persistence and are exposed in
hotspot `quality_flags`; no database column or migration is needed for this
additive DTO field. See [inference protocol](inference-and-explanations.md).

## Phase 4.7 registry and run pins

`model_versions.training_manifest` uses model-registry-v1 for managed approvals,
binding evaluation/release hashes, review, approved geographic/mode scope and an
approval-event UUID. `metrics` stores final forest/baseline metrics and paired
whole-event uncertainty. `artifact_path` resolves inside MODEL_DIR to the compact
bundle; `artifact_hash` pins that bundle manifest.

`model_activation` stores active_model_id, previous_model_id and revision.
`model_promotions` records model_id, previous_model_id, action, reviewer, rationale,
created_at and release_sha256. Run orchestration's scorer object stores model ID,
version, manifest SHA256, approval reference and private managed path. These pins
cannot be rewritten on resume; scores persist their actual model_id and nullable
probability. The registry does not modify saved alerts or retrospective scores.
