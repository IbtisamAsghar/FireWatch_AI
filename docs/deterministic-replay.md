# Deterministic historical replay (Phase 3.6)

Three real-data scenarios are declared in `configs/replays.yaml`. The local bundle
root defaults to `data/replays`; `REPLAY_BUNDLE_DIR` can point to a relocated copy.
The candidate region can remain disabled for live collection. Verified bundle
replay neither enables a region nor advances the latest-live pointer.

```powershell
.\.venv\Scripts\firewatch.exe replay --scenario norcal-active-v1
.\.venv\Scripts\firewatch.exe replay --scenario norcal-quiet-v1
.\.venv\Scripts\firewatch.exe replay --scenario norcal-degraded-v1
# Equivalent module invocation:
.\.venv\Scripts\python.exe -m firewatch.cli replay --scenario norcal-active-v1
```

The CLI verifies and imports local inputs, admits a durable job, and executes it
synchronously using the worker's lease, heartbeat and stage checkpoints. It prints
JSON containing job/run UUIDs, status, historical cutoff, processing timestamp,
feature hashes and scores. If another worker owns the cutoff, the job stays queued
and the CLI reports its job ID. Use the authenticated existing job status endpoint
or let the dedicated worker consume it. The HTTP replay submission endpoint uses
the same catalog/bundle digest and worker path after inputs have been imported.
Clients cannot select arbitrary asset paths, cutoffs, provider URLs or versions.

## Prepared real scenarios

- `norcal-active-v1`: 25 July 2024 at 22:00 UTC, 832 candidate NOAA-20 archive
  observations and six snapshots. GFS historical weather and fixed POWER context
  are bundled. The wide DBSCAN cluster quality warning remains visible.
- `norcal-quiet-v1`: 6 May 2024 at 12:00 UTC, complete archive-day inputs and zero
  candidate/history observations in the relevant window. It completes with empty
  features/scores/evidence. No enrichment cells are needed for an empty snapshot set.
- `norcal-degraded-v1`: 25 July 2024 at 23:00 UTC, six snapshots and an incomplete
  cached weather interval. The cache ends before the newest required hour, so all
  six rolling rainfall values remain null/flagged. No source record was edited or
  artificial outage inserted. This is actual incomplete *local cache coverage*;
  it does not establish an upstream provider outage.

[Catalog](replay-catalog.json) records source IDs, counts, bundle checksums and
coverage notes. These are satellite-observed activity scenarios, not independently
verified fire incidents, physical severity labels or a strict operational backtest.
Only NOAA-20 SP is pinned here; NOAA-21 coverage is not implied. Missing detections
in a valid archive interval do not prove fire absence or cloud-free observation.

## Bundle contents and portability

Each scenario directory contains `manifest.json`, `canonical.json`, and
content-addressed `raw/<sha256>.bin` files. The versioned `replay-bundle-v1` manifest
records:

- Fixed scenario ID/window/region, scorer and feature versions, feature/clustering
  configuration and an explicit `real` versus `synthetic` data label.
- Exact satellite batch/revision policy and enrichment pins, including missing
  cells, partial weather revisions, baseline definitions and availability cutoff.
- Every raw/canonical file's relative name, byte size and SHA-256.
- Baseline source dependencies, raw weather bytes and original archive bytes,
  retaining their provider retrieval timestamps and preserved UUIDs.

`canonical.json` holds only the eight allowlisted input tables: regions, source
assets, ingestion batches, detections, source/region associations, weather, and
climate baselines. It includes all twelve baseline months and referenced annual
POWER sources. It never contains jobs, runs, scores, alerts, live heads, credentials
or serialized executable models. File references are relative and rebased on import.
Original application storage paths are not exported. SHA-256 detects changes; the
operator-controlled catalog is the trust root, not an authenticity signature.

Copy the **complete scenario directories** together with the matching catalog.
A clean database imports them automatically through the replay CLI. Existing UUIDs,
values and raw bytes must agree; conflicts roll back atomically. Existing region
UUID/name conflicts are rejected rather than silently remapped, because UUIDs bind
snapshot and feature identities. Import bundles before seeding a same-named region
with a different UUID into a fresh database. Replay imports always create disabled
regions and preserve an existing region's enablement.

Bundles are prepared locally under ignored `data/`, following the repository's
policy against committing downloaded data. The code, preparation specification,
catalog hashes and sanitized acceptance reports are versioned. A clone therefore
needs the prepared bundle directories or the original imported/cache inputs;
Git alone does not provide downloaded source bytes. Do not discard the local
bundles if another machine needs offline replay.

The bounded exporter requires a new directory and never overwrites a published
bundle. With the already imported seasonal archives and weather/POWER cache:

```powershell
.\.venv\Scripts\python.exe -B scripts/prepare_replays.py
```

`configs/replay-preparation.yaml` declares the three cutoffs and honest coverage
notes. Preparation only reads source data and rolls back its temporary snapshot
runs. Raw source checksums and configured-secret scans must pass before a final
manifest is published. Rebuilding intentionally uses a new prepared manifest and
catalog checksum; preserve the reviewed catalog/bundles together.

## Determinism, clocks and isolation

Replay calls the shared Phase 3.3/3.4/3.5 pipeline. It has no provider client,
forecast fallback, baseline download, scheduler, or blockchain submission. Bundle
files and database canonical records are checked again by the worker, even when
admission happened earlier. Future or unrelated cache revisions cannot replace
pinned weather/baselines. Source provenance and feature values remain retrospective.

A replay run key binds the region UUID, bundle digest and historical cutoff. Same
bundle/config re-admission returns the existing completed run without duplicate
outputs. A new reviewed bundle digest creates a separate run. Concurrent jobs still
serialize by region/mode/cutoff through the durable queue. Legacy Phase 3.5 jobs
retain their original run keys and frozen request compatibility.

`as_of` and acquisition/valid times always stay historical. The injected `now`
callable drives lease management and actual processing/scoring/completion times;
replay never replaces it with the historical cutoff. Fresh-database runs at different
processing times preserve identical numeric results while exposing their real later
processing timestamps. Re-reading an already completed run preserves its original
processing provenance.

Within the exact locked Python/scikit-learn environment, acceptance achieved exact
feature hash and baseline numeric equality across fresh databases and relocated
bundles. The documented numeric comparison tolerance is `1e-12`; changing package,
algorithm, schema, scorer or bundle versions requires a new compatibility review.
Operational timestamps/job IDs are excluded from numeric determinism comparisons.
All outputs retain `mode=replay`; baseline scores always have null probability.

## Acceptance

```powershell
.\.venv\Scripts\python.exe -B scripts/verify_replays.py
.\.venv\Scripts\python.exe -B scripts/verify_bootstrap.py
```

The real replay verifier disables provider HTTP and uses fresh disposable schemas
in `firewatch_test`. It executes all scenarios twice per database, including a
relocated bundle copy, and compares run IDs, feature hashes, numeric scores,
priorities, completeness and warnings. It confirms zero provider HTTP attempts,
empty-run semantics, degraded rainfall nulls, disabled regions and unchanged live
heads. Results: [fresh-database smoke](phase-3.6-smoke.json) and
[actual CLI application runs](phase-3.6-cli-smoke.json).

Synthetic provider-shaped security/failure tests are explicitly labeled synthetic
and use temporary bundles/catalogs. They are separate from the real scenario
catalog and smoke reports. Tests cover file tampering/removal, traversal, duplicate
JSON keys, nonfinite values, import conflicts/rollback, altered configuration,
future cache revisions, disabled regions and clock-independent computation.

See [Phase 3.6 acceptance](phase-3.6-checklist.md). No model skill, calibrated
probability, blockchain anchoring or production deployment is claimed by replay.
