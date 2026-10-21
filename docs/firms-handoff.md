# FIRMS acceptance and source handoff

Phase 2.6 closes Module 2 source ingestion. The FIRMS adapter, archive importer and
48-hour refresh all produce the existing `DetectionInput` contract through
`firms-normalizer-v1` and `firms-observation-v1`. This phase adds offline verification
and a consolidated source bundle. No additional dependency or migration is needed.

## Verify and export

From the repository root, using the configured virtual environment and local `.env`:

```powershell
./.venv/Scripts/python.exe -m scripts.verify_firms_handoff --archive data/archive-imports/dcef1731-e361-5a9e-b21b-386fa33741ef/handoff.json --archive data/archive-imports/1e19cf50-bfdb-5035-9c0a-6cf41742bc1b/handoff.json --refresh data/firms-refresh/f6e1d4f1-6ac0-5b7b-8d03-918c65961f47/handoff.json --output data/member-4/firms-acceptance-new
```

Repeat `--archive` or `--refresh` for additional completed inputs. The output directory
must be new. The command performs no HTTP requests or database writes. It scans
tracked and untracked nonignored repository files plus the supplied source artifacts
for the configured database URL/password, operator token and FIRMS MAP_KEY. Matches
fail with fixed messages, without printing the matched value. This is a scan for
known configured secrets; it does not certify absence of unknown credentials.
Existing adapter tests separately exercise redacted logs, exceptions and discarded
provider credential echoes.

The exporter checks original CSV SHA-256, agreement between quality/handoff reports,
row totals, every revision hash, normalized schema/units, product/date bounds and
one distinct batch for every declared calendar day. The SQLite index is read-only.
Refresh validation recomputes the product coverage summary, validates record hash,
count, product, batch membership, uniqueness and exact acquisition window. Validated
refresh records are copied and checked again. Corruption and configured secrets
prevent publication of the final `manifest.json`. Incomplete folders without that
manifest must never be consumed. Existing successful exports cannot be overwritten.

The final manifest binds these portable files by SHA-256:

- `archive-revisions.jsonl`: every unique revision snapshot, including corrected
  measurements, with import UUID, daily source batch UUID, coverage flag and a
  `RowRevision` containing `DetectionInput` and bounded source row-number samples.
- `refresh-<uuid>.jsonl`: selected live revision observations in the existing
  `SelectedDetection` envelope, copied byte-for-byte from the validated refresh.
- `observation.schema.json`: generated JSON Schema for the common observation DTO.
- `ingestion-quality.json`: reconciled per-source counts, coverage/inventory,
  refresh summaries and explicit evaluation limitations.

The manifest also contains original public NASA provenance, source hashes, daily
batch IDs and normalizer/key versions. Original CSVs and full row-number provenance
remain in the original private archive-import directories; the bundle contains
normalized provider rows, so treat it as data and keep it outside Git. It is not a
frozen Run, a set of labels or an independent event grouping.

## Consumer rules

Use the shared units: acquisition UTC, coordinates degrees, FRP MW, I4/I5 brightness
K, scan/track km. Zero FRP is valid; missing values remain null. Confidence categories
are not calibrated percentages. Original provider columns survive normalization;
NASA archive `brightness`/`bright_t31` aliases map to the same physical measurements
as `bright_ti4`/`bright_ti5`.

Select source revisions through `RevisionPolicy`, explicitly pinning product per
sensor, batch IDs and retrieval cutoff; then freeze inputs on a Run. The policy
accepts at most 128 batches, so select the dates needed for each processing window
rather than all 186 seasonal batches at once. Consume selected revision values for
corrections. Do not train from canonical first-seen observation values or union
superseded daily snapshots. The archive JSONL preserves all corrections and can
include multiple revisions per dedup key; it is not a ready-to-train table.

Incomplete or failed product coverage is unknown. Header-only successful CSV is a
known empty source response, which does not prove absence of fires or cloud-free
coverage. Counts across multiple imports can overlap; aggregate source totals are
not a global unique observation count.

## Historical evaluation limitation

The real seasonal inventory contains 17,862 unlabeled NOAA-20 SP observations over
372 calendar days in 2022 and 2024. Its 43 temporal activity-period proxies use a
gap greater than 24 hours. They are not reviewed independent wildfire events.
Independent event count and positive/negative class counts remain unknown. The
exporter conservatively keeps event-separated evaluation and supervised training
readiness false; it does not accept event labels or infer readiness from proxy
counts. Later evaluation needs reviewed event groups, leakage-aware splits, labels
and the historical weather/baseline context required by Modules 3 and 4.

See [acceptance evidence](phase-2.6-checklist.md),
[historical inventory](historical-data-inventory.json),
[archive importer](firms-archive-import.md),
[revision selection](firms-normalization.md) and [refresh guide](firms-refresh.md).
