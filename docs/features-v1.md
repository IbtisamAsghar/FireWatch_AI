# features-v1: definitions and reproducibility

Phase 3.4 implements one shared past-only feature builder for training, replay and
live inference. It creates validated feature records, not labels, scores, a trained
model or a completed pipeline Run. Phase 3.5 will orchestrate these stages.

## Feature values and units

The model value whitelist has exactly 17 fields:

- `frp_max_mw`, `frp_sum_mw` (MW): maximum/sum of known FRP among current snapshot
  members. All missing gives null. Partial aggregates retain the Phase 3.3 flags
  and valid/missing member counts; missing FRP never becomes zero.
- `brightness_max_k` (K): maximum valid I-4 brightness among current members. It
  uses the normalized I-4 field, not I-5 or raw aliases. All missing gives null;
  partial availability is flagged.
- `detections_6h`, `detections_12h`, `detections_24h` (observation counts): selected
  observations within 2 km of the spherical centroid in `(T-lookback,T]`.
- `hours_since_first_seen_24h` (h): T minus the earliest centroid-near observation
  strictly inside the 24-hour lookup. It is null if the centroid has no such
  observations, and is not a full incident age or an event tracking claim.
- `confidence_high_fraction` (0–1): high / (low + nominal + high) among current
  members. Unknown classes are excluded and flagged; all unknown gives null.
  This is a category fraction, not a calibrated confidence probability.
- `day_fraction` (0–1): daytime members / all current members when every member
  has a known D/N value. If any value is unknown, the fraction is null and flagged;
  unknown does not silently mean nighttime. All known nighttime gives valid zero.
- `temperature_c` (C), `humidity_pct` (%), `wind_ms`, `gust_ms` (m/s): the last valid
  preceding completed UTC weather hour, using one cell/product/model/revision.
- `precipitation_24h_mm` (mm): sum of all 24 preceding complete hourly precipitation
  intervals. A single missing hour/value makes this null, not a smaller sum.
- `temperature_departure_c` (C): preceding temperature minus the fixed monthly
  mean daily climate temperature; not a standardized hourly heat anomaly.
- `rainfall_deficit_24h_mm` (mm): max(0, monthly reference daily rain amount minus
  preceding 24-hour rain). Requires complete rainfall and a valid baseline.
- `weather_age_hours` (h): T minus the selected valid weather timestamp. Staleness
  is a quality flag at age strictly above the configured three-hour default.

Features-v1 fixes the Phase 3.3 2 km spatial definition and 6/12/24-hour windows.
A changed clustering definition is rejected rather than mislabeled features-v1.
DBSCAN chaining means centroid-near counts may differ substantially from member
count. The real wide-cluster warning from Phase 3.3 is preserved in feature lineage.

## Missing indicators and input exclusions

Each of the 14 nullable values has a boolean `<feature>_missing` indicator. The
three counts are nonnullable; source-day coverage is required by the snapshot
stage and source quality limitations carry through. Genuine numeric zero has a
false missing indicator. NaN/infinity, booleans in numeric fields, wrong units,
extra keys and inconsistent count/missing semantics are rejected.

`FeatureValues` validates the values. `FeatureSet.values` contains exactly those
17 numeric/nullable fields. `FeatureSet.missing_flags` stores the corresponding
14 booleans without suffixes. `model_inputs(feature)` validates schema, units,
missing flags and integrity, then returns exactly the 31 ordered model inputs:
17 values plus 14 suffixed missing indicators. It does not impute nulls.

IDs, absolute coordinates, event/risk/future labels, future sensor counts, model
scores, priorities, chain/anchored status and timestamps are excluded from model
inputs. Context IDs, cutoff, source coordinates and provenance remain outside that
whitelist for auditing/maps/grouping. Training-fitted preprocessing belongs to
Module 4; this builder performs no fitting or automatic substitution.

See the [machine-readable schema and ordered inputs](features-v1.schema.json).

## Weather and climate joins

The centroid maps to the existing versioned 0.1-degree weather request cell.
The weather cell center then maps to the coarse POWER climate cell, so the daily
reference corresponds to the weather point even near coarse-cell boundaries.
Requested and returned weather coordinates remain distinct in the pinned DTOs.

The default replay/training product is GFS historical forecast; live uses the GFS
live product. Explicit ERA5 reanalysis requires matching configuration and is
flagged; there is no automatic product fallback. The builder verifies cell, source,
model, UTC times and units against the declared semantics.

For non-hour T, weather selection and rainfall end at floor(T). The 24 required
hourly sums end at floor(T)-23h through floor(T). Future hours are excluded from
values and scoped weather hashes. Daily POWER values only supply the historical
2010–2019 climate reference; no daily value spanning future hours is current weather.

Cached selection first uses the Phase 3.1 complete-revision cache. If unavailable,
it can use the latest single partial revision in the requested interval. It never
stitches different revisions. A partial series can supply current temperature while
withholding rainfall/deficit, with explicit flags. Missing cache or baseline values
remain null with missing flags; they do not trigger live HTTP or imputation.

## Shared builder and provenance

`build_features(snapshot_batch, selected_revisions, enrichments, satellite_assets,
config)` is the pure shared interface. It has no clock, database, HTTP client,
training labels or scorer. It checks scoped history hashes, current memberships,
centroid/FRP consistency, spatial counts, source identities and enrichment units.
Input and output ordering are deterministic. Configurations declare feature version,
weather identity/namespace, stale threshold and fixed climate reference.

Each `FeatureSet` stores values, missing flags, units, cutoff/version, snapshot/run
context and SHA-256 of its complete canonical payload excluding the hash itself.
Lineage binds source asset IDs/hashes, selected member revisions, snapshot input
hash, scoped weather hash/product/model/version/time, baseline identity/reference
and value hash, configuration, quality flags and valid-member counts. The feature
hash binds provenance and context too; equal numeric vectors from distinct Runs or
modes intentionally have different integrity hashes.

## Atomic persistence and immutable retries

`build_run_features(session, run_id, config, retrieval_cutoff=...)` runs inside a
caller-owned transaction using saved snapshot inputs and cached enrichment only.
It locks the Run, verifies frozen snapshots, selects enrichment once, saves the
pins and feature hashes in `runs.input_asset_manifest.features`, and writes one
`feature_sets` row per snapshot. No migration, dependency or shared API DTO change
is needed. Empty snapshot Runs have an explicit successful empty feature manifest.

Source availability cutoff is separate from historical T. Satellite/weather/
baseline sources must be available by the declared retrieval cutoff. Later-retrieved
historical products are retrospective inputs, not proof of issue-time availability.
Default availability is current time on first construction, then frozen on retry.
An explicitly different cutoff/config is refused for an existing feature stage.

Raw weather JSON is rehashed/reparsed and compared with pinned normalized database
rows. Baseline reads validate their bound manifests and database values. Pins retain
missing inputs as missing: even backfilled rows with old retrieval timestamps cannot
silently fill or change a saved feature set. Extra future records, new corrections,
new weather revisions or new baselines do not change prior hashes. Tampered source,
manifest or stored feature rows fail clearly rather than being silently repaired.

Run-level locking serializes concurrent retries. Failure rolls back feature rows,
pins and stage state together. Enrichment/features stages become succeeded even
when optional context is missing, because missingness is an explicit computation
result. Global coverage is not promoted to complete. Scoring/evidence/publication
remain pending; latest live is untouched.

## Operator commands and explicit refresh

From the existing environment, after source/cache preparation:

```powershell
.\.venv\Scripts\firewatch.exe build-features --run-id cef85e0d-2a9e-5343-b928-e473bb20683b
.\.venv\Scripts\firewatch.exe build-features --run-id cef85e0d-2a9e-5343-b928-e473bb20683b --fork
```

`--config` defaults to `configs/features-v1.yaml`. `--retrieval-cutoff` optionally
pins explicit aware source availability. Every command uses cached data only.
Prepare missing weather/baselines with the Phase 3.1/3.2 commands first if full
context is wanted; otherwise the initial feature stage intentionally freezes nulls.

`--fork` explicitly creates a new incomplete Run with the same frozen satellite
inputs and new run-specific snapshot IDs, then selects enrichment afresh. The
original Run and features remain unchanged. Reuse the returned new run ID for
idempotent retries; repeating `--fork` intentionally creates another Run. Changed
satellite selections belong in a new snapshot Run instead.

The summary reports feature counts, missing-weather/climate counts, hashes and
`pipeline_completed=false`. Resource bounds include the snapshot history/neighborhood
limits, at most 256 weather cells by default and 32 MiB of weather evidence per stage.

See [Phase 3.4 acceptance](phase-3.4-checklist.md) for verification evidence.
