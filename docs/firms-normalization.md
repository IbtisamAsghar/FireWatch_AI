# FIRMS normalization and revision selection

Phase 2.3 converts completed bounded Phase 2.2 fetches into validated observations,
quality evidence and optional transactional PostgreSQL writes. Seasonal archive
import/chunking belongs to Phase 2.4; scheduled refresh belongs to Phase 2.5.
No new dependency or database migration is required.

## Operator commands

From the repository root with the project's virtual environment active:

```powershell
firewatch normalize-firms --manifest data/raw/firms/<fetch-id>/manifest.json
firewatch normalize-firms --manifest data/raw/firms/<fetch-id>/manifest.json --persist --region northern-california
```

The first command is offline and does not load settings, contact NASA or access
PostgreSQL. It verifies the completed fetch manifest, CSV size, SHA-256, product,
request metadata and retrieval timestamp before normalizing. Evidence must remain
in its owning fetch directory. Failed/partial fetches and altered files are refused.
The second command requires configured database access and an existing region
whose bbox exactly matches the fetch. It commits assets, batch counts, canonical
observations and lineage in one transaction; failure rolls back those writes.
It does not enable a region or create a processing run.

`--invalid-frp reject` is the default; `--invalid-frp null` preserves a flagged
missing measurement instead. Missing FRP is allowed by default; use
`--missing-frp reject` to reject it. FRP zero is valid. Policy and normalizer
version contribute to the policy hash and ingestion batch identity. Repeating the
same fetch, policy and region reuses its completed batch. An independent fetch
creates another lineage association and still uses database observation uniqueness.

## Field semantics

- Acquisition date is strict `YYYY-MM-DD`; acquisition time is one to four ASCII
  digits, left-padded to `HHMM` and validated as UTC. `37` and `0037` mean 00:37 UTC.
  Observations must lie inside the requested half-open UTC calendar-day window.
- Coordinates must be finite decimal values in geographic bounds and the requested
  bbox. No coordinate rounding is applied when constructing identity.
- FRP is MW. Nonfinite, negative and nonnumeric values follow the explicit policy.
  Brightness is Kelvin; scan/track are km. Invalid optional measurements become
  null with quality flags, preserving their original strings. Both VIIRS thermal
  channels are retained: I4 in `brightness_k`, I5 in normalization metadata.
- Confidence `l/n/h` (or their spelled-out equivalents) maps to low/nominal/high.
  Unknown or numeric values remain unknown, with the exact original confidence
  retained. No percentage conversion is performed.
- Product is supplied by the validated source request. Satellite aliases are checked
  against NOAA-20/21, and instrument must be VIIRS. Raw processing version and its
  interpreted SP/NRT/URT/RT status are retained. An explicit version inconsistent
  with the requested SP versus non-SP product is rejected; unrecognized versions
  are preserved and flagged. Day/night is D/N or flagged null.
- Every accepted revision retains all original row fields under `provider_row`.
  Exact duplicate row numbers are retained on the revision. Rejected rows retain
  their complete provider values and all rejection reasons.

These mappings follow NASA's [FIRMS API examples](https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html)
and [VIIRS Collection 2 active-fire guide](https://www.earthdata.nasa.gov/s3fs-public/2024-07/VIIRS_C2_AF-375m_User_Guide_1.0.pdf).
Satellite thermal anomalies are observations, not confirmed wildfire incidents.

## Identity and evidence

`firms-observation-v1` hashes canonical JSON containing product, canonical sensor,
UTC acquisition timestamp, exact decimal latitude/longitude, and scan/track where
available. Decimal source values are used directly, without conversion through a
rounded float. Insignificant trailing zeros and signed zero normalize consistently;
original coordinate strings and decimal-place counts remain in raw metadata.
Different NOAA satellites and different products cannot share an observation key.
Absent scan/track uses null; invalid scan/track retains a hash of the invalid text
in the identity so unrelated invalid values are not silently conflated.

A product-independent `physical_key` links exact matching NRT/SP identities while
retaining the sensor. This is exact lineage only: changed time, location or footprint
does not claim a fuzzy cross-product match. Each full normalized snapshot receives
a SHA-256 revision hash. Mutable FRP, confidence, brightness and processing version
are excluded from observation identity but included in revision identity.

Quality files are written beside the untouched CSV:

- `quality-<policy-hash-prefix>.json`: counts, policy/version, source checksum,
  rejection/flag totals and at most 20 rejection samples by default. Samples cap
  columns and value lengths to keep the summary small.
- `rejected-<policy-hash-prefix>.jsonl`: every rejected row and its reasons in full.

Quality `accepted_count` counts distinct observation identities. `duplicate_count`
includes additional valid rows for those identities, including correction variants;
`revised_count` distinguishes extra unique revision snapshots. Ingestion
`inserted_count` counts new global database identities, so previously saved rows
become duplicates even if they were unique within this CSV. `source_revision_count`
counts snapshots attached by this write. A reused batch inserts no new snapshots.

`source_assets` retains the source request and raw SHA-256. `ingestion_batches`
retains transactional totals. `detections.dedup_key` is protected by PostgreSQL's
unique constraint; `ON CONFLICT` resolves identity across independent imports,
regions and concurrent transactions. `detection_sources.raw_values` uses
`firms-source-revisions-v1` to retain all variants associated with each source batch.
The canonical detection keeps its first values. **Processing must use the selected
revision observations below for measurements**, because the canonical row is an
identity record and can have older FRP/confidence.

## Deterministic run inputs

`RevisionPolicy` requires one explicit product per sensor, unique ingestion batch
IDs and a retrieval cutoff. Unknown, failed, wrong-region, unavailable-at-cutoff or
unselected-product batches are rejected. Batches with rejected rows fail selection
by default; accepting partial row coverage requires `allow_rejected_batches=True`.

For overlapping full-region UTC calendar days, the latest retrieved eligible batch
wins for the entire product/day; UUID breaks equal retrieval-time ties. A valid
empty later response therefore removes stale prior detections. A correction that
moves a hotspot cannot accidentally add the old location back. Within a single
batch containing conflicting variants, the greatest revision hash is a deterministic
tie-break, not a claim about provider revision chronology. All variants remain saved.

NRT and SP can coexist in storage, but a run chooses only one for each satellite.
Even corrections that change geometry across products cannot be counted twice in
that run. NOAA-20 and NOAA-21 remain distinct observations. No cross-satellite fire
event clustering is introduced here.

Call `freeze_firms_inputs(session, run_id, window_start, policy)` in a write
transaction before downstream processing. It selects observations through the run's
inclusive `as_of` cutoff and pins policy, source batch IDs, detection IDs and exact
revision hashes in `Run.input_asset_manifest["firms"]`. Repeating the call returns
the pinned snapshots; attempting to change policy/window fails. Later source
corrections do not change an existing run. Revision hashes are checked on retrieval;
the existing completed-run database guard also prevents manifest edits. Use
`select_revisions` for an explicit half-open exploratory window without pinning.

These functions are the handoff for the later processing pipeline. They do not
start a scheduler or compute clusters, features or scores. Legacy source rows from
older helpers without versioned revision history fail closed if selected here.

See [acceptance and validation](phase-2.3-checklist.md) and the
[aggregate real-data evidence](phase-2.3-smoke.json).
