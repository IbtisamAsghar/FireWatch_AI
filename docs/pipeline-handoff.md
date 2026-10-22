# Member 3 acceptance and handoff (Phase 3.7)

The complete time-matched pipeline is available: original FIRMS observations and
revision pins, historical/live weather cache, fixed-reference POWER baselines,
snapshots/features, durable jobs, local evidence and offline historical replay.
Phase 3.7 closes acceptance and produces a portable handoff for Members 4 and 1.

## Ready artifacts

The actual local package is `data/handoffs/module-3-v1/` (Git-ignored data).
[Handoff receipt](phase-3.7-handoff.json) records its exact manifest checksum,
file checksums, counts, provenance label and verified feature hashes.

- Member 4: `features.parquet`, `memberships.parquet`, `schema.json`, and
  `provenance.json` contain 12 feature snapshots and 1,664 snapshot/source links
  across the real active, quiet and degraded replay scenarios.
- Member 1: `runs.json` and `jobs.json` contain the existing public run/status
  DTOs (`api-v1`), including stages, coverage, source freshness, attempt/lease
  information, historical cutoff and actual later processing timestamps.
- Shared: `manifest.json` binds all seven payload files; `README.md` describes
  consumers and limitations. Verify the package before consuming it.

The quiet run is represented in run metadata despite contributing no feature rows.
The degraded run preserves null rainfall and missing flags. The active/degraded
wide-cluster quality warning remains visible; no cluster definition changed.
Run/status examples are snapshots captured at export time, not live status polling.

## Commands

Use the completed UUIDs printed by `firewatch replay`, repeating `--run-id` for each
selected run. Existing directories are never replaced.

```powershell
.\.venv\Scripts\firewatch.exe export-pipeline-handoff --run-id <UUID> --output data/handoffs/new-package --data-kind real
.\.venv\Scripts\firewatch.exe verify-pipeline-handoff --path data/handoffs/module-3-v1 --manifest-sha256 6395e9ff7177c1a4c1eec3bbc708f84bbe90c293838b0ccb841a66a0865ce5c7
.\.venv\Scripts\python.exe -B scripts/verify_pipeline.py
```

The exported package is self-contained for reading Parquet and run/status data;
verification needs no database or provider access. It does not contain raw provider
bytes: preserve the matching `data/replays/` bundles separately when source-level
replay is needed. Copy the whole handoff directory and retain its reviewed manifest
hash. SHA-256 detects changes; it is not an external authenticity signature.
No downloaded dataset or private runtime configuration is added to Git.

## Member 4 data contract

[Committed schema](pipeline-handoff.schema.json) and the package's `schema.json`
identify every field, Arrow type, nullability, role, unit and model-input order.
There are exactly 31 allowed model inputs: the 17 features-v1 values followed by
14 boolean `<name>_missing` indicators. Model-input selection must use that list.
UUIDs, region/mode, coordinates, timestamps, member count, feature hashes and lineage
are metadata; they are excluded from model inputs. Predictions, priority scores,
labels, event IDs and partition assignments are not feature columns.

UUIDs are strings, counts are int64, feature measurements are float64 and indicators
are booleans. Nullable values remain Arrow nulls; real zero remains zero. Acquisition,
cutoff and processing times use UTC microsecond timestamps. Units are both field
metadata and an explicit schema mapping. Parquet is written with version 2.6 and
Zstandard compression using pinned PyArrow 21.0.0. The supported writer API is
[Apache Arrow 21 write_table](https://arrow.apache.org/docs/21.0/python/generated/pyarrow.parquet.write_table.html).

`lineage_json` preserves exact canonical feature lineage. `provenance.json` includes
frozen satellite revision policy, snapshot diagnostics, feature config, weather and
climate pins, hashes and source asset references. `memberships.parquet` joins
`(run_id,snapshot_id)` to detection/batch/revision and acquisition time. Each member
is unique within its snapshot and acquired during `(T-6h,T]`. The verifier rebuilds
FeatureSet DTOs from Parquet and checks their original hashes, missing flags and
units, alongside source joins and typed run/status records.

This is an **unlabeled feature handoff, not a training-ready dataset**. It provides
three small retrospective scenarios with overlapping active/degraded observations.
Event grouping, future labels, follow-up completeness, chronological splits,
purging, imputation fitting and evaluation remain Module 4 tasks. Do not use these
rows as independent held-out fire events or claim validated predictive skill.

## Validation and publication behavior

Only existing completed runs are eligible. Export reuses the shared frozen feature
builder to verify database membership, revisions, cached evidence and saved feature
integrity before serialization. A missing/mutated source or incompatible feature
schema fails rather than silently exporting changed values. Database transactions
close before Parquet serialization.

Plaintext metadata is screened for configured secrets, private keys/paths and URLs
before compression. The package uses fixed relative filenames; no source storage
paths, DSNs, operator tokens or provider request URLs are exported. Row/file size
bounds and exact schemas constrain verification. All files are written in a new
staging directory, read back, semantically verified and then published together.
A failure cleans only that validated staging directory and leaves no partial final
package. Existing outputs are preserved. An empty run still gets typed zero-row
Parquet files and complete run/status/provenance manifests.

The data-kind label is an operator declaration: `real`, `synthetic`, or
`unclassified`. The real receipt is tied to the prepared real scenario catalog;
synthetic test exports are explicitly labeled synthetic and kept separate.

## Consolidated acceptance evidence

[Real smoke](phase-3.7-smoke.json) uses a disposable migrated `firewatch_test` schema.
A separate Python process terminates without cleanup immediately after committed
features (exit 23). The next worker reclaims its expired lease, completes on attempt
2, and produces no duplicate alerts. Lease expiry is accelerated only inside that
schema. All three real scenarios then replay and export/verify together.

Dedicated child processes install an irreversible database-only network guard.
Python DNS/socket connections are restricted to the configured PostgreSQL endpoint;
httpx synchronous/asynchronous and urllib provider transports are denied. Replay and
Parquet acceptance record zero denied attempts. This is process-level isolation
covering the project's provider network paths, not a host firewall or arbitrary
native-code sandbox. Native libpq remains available for PostgreSQL. A separate
synthetic subprocess test proves outbound DNS/HTTP attempts are actually blocked.
The guard is never installed in the API, ordinary worker or caller's interpreter.

The acceptance matrix uses existing meaningful regressions plus new handoff tests:

- Source/snapshot/feature joins: snapshot and feature persistence tests, canonical
  import conflicts, and Parquet orphan/duplicate membership rejection.
- Time boundaries: `(T-6h,T]`, `(T-24h,T]`, future exclusion, frozen retrieval cutoffs
  and metadata/acquisition separation in clustering, feature and handoff tests.
- Distance: closed 2 km boundary, haversine threshold, antimeridian/high latitude,
  spherical centroid and DBSCAN chaining diagnostics in cluster processing tests.
- Missingness/units: exact feature unit mapping, nonfinite rejection, genuine zero
  versus missing FRP, nullable Parquet, and unknown confidence/day-night behavior.
- Rainfall/fills: complete 24 completed-hour interval, non-hour cutoffs, missing
  hour/variable, future exclusion, provider-declared POWER fill sentinels and invalid
  values becoming null without precipitation imputation.
- Empty results: successful real quiet replay, existing empty feature/worker tests,
  and typed zero-row Parquet handoff with one completed run/status record.
- Recovery/offline: checkpoint tests, actual child-process termination and the new
  database-only isolated acceptance run.

See [Phase 3.7 checklist](phase-3.7-checklist.md) for final verification results.
No emergency classification, model skill, chain submission or deployment is implied.


## Pre-Phase 4 input gate

Run the offline assessment against the reviewed package hash:

```powershell
.\.venv\Scripts\firewatch.exe assess-pipeline-handoff --path data/handoffs/module-3-v1 --manifest-sha256 6395e9ff7177c1a4c1eec3bbc708f84bbe90c293838b0ccb841a66a0865ce5c7
```

Policy `compact-centroid-inputs-v1` requires every current cluster member to lie
within the closed 2 km centroid radius and requires complete current FRP. Wide
DBSCAN chains and missing/partial FRP baselines are excluded from the proposed
centroid target inputs. This resolves the default spatial mismatch conservatively:
whole-cluster FRP cannot set a target baseline for a smaller, different footprint.
No saved cluster, feature hash, replay, score or alert is recomputed or overwritten.
A different footprint requires a newly versioned definition and reviewed dataset.

The existing real package contains eight compact-target candidate rows and four
excluded wide rows. All 832 unique member observations are reused across snapshots;
the twelve rows are not twelve independent events. The report lists row decisions,
reason counts and reuse counts, always with `training_ready=false`. Input eligibility
does not certify follow-up, labels, class balance, event independence or splits.
Future label construction must apply the declared policy using the shared
`centroid_target_exclusions` helper before using a current FRP baseline.

Verification additionally rejects duplicate JSON keys/nonfinite values, required
Parquet nulls, duplicated runs/jobs/snapshots/source pins, mismatched region/context,
extra feature-hash entries, broken snapshot-stage binding, and feature lineage that
disagrees with the saved source revisions, configuration or clustering input hash.
Malformed structures produce a validation error. The existing v1 package format
and its reviewed manifest digest remain valid.
