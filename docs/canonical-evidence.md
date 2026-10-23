# Canonical alert evidence: Phase 5.1

Newly admitted runs use the pair `schema_version=evidence-v1` and
`canonicalization_version=evidence-json-v1`. The schema is published in
[evidence-v1.schema.json](evidence-v1.schema.json). The pure builder lives in
`firewatch.evidence.payload`; encoding lives in `firewatch.evidence.serialization`.
No provider, database, chain, clock, model fitting or executable artifact loading
is performed by the builder/encoder. The worker supplies already saved scores
and frozen inputs. Evidence is generated for the existing Critical-alert policy;
this phase does not change which scores become alerts.

## Contents and source binding

The payload saves alert, score, run and snapshot IDs; live/replay mode; region ID,
name and bbox; historical UTC cutoff and actual scoring time; snapshot centroid,
membership and clustering version; full versioned feature values, units, missing
flags, lineage and original feature hash; scorer version, score kind, priority and
nullable probability; ordered reasons, explanation and combined run/scorer warnings.

Satellite provenance includes the complete frozen 24-hour selection policy,
detection IDs, batch IDs and revision hashes, raw asset IDs/checksums and the
snapshot input hash. It covers the history used by activity features as well as
current cluster members. Weather contains the selected cell/product/model,
selected valid time (null when absent), precipitation interval end, all pinned
hourly records used in the preceding-day interval, retrieval times and checksums.
Climate context includes the actual monthly baseline, reference years, version,
asset/checksum, statistics and coverage metadata, or explicit nulls when missing.
The source checksum inventory is unique by asset ID; conflicting or missing pins
are rejected. A supervised scorer additionally saves its model ID, scorer version,
inference-manifest digest and approval reference, without leaking local paths.
An artifact-free baseline has explicit null model fields and null probability.

The envelope also binds the run configuration and feature-stage hashes and the
feature retrieval cutoff. Validation rejects mismatched contexts, member revisions,
source pins, weather/baseline identities, model approvals and probability claims.
It does not confer approval on a model or independently authenticate a source.

## Exact encoding contract

1. Encode JSON as strict UTF-8 without BOM, trailing newline or whitespace outside
   strings. Keys sort by Unicode code point; use compact comma/colon separators.
   Strings use JSON escaping for quotes, backslashes and control characters.
   Use the short escapes `\b`, `\f`, `\n`, `\r`, `\t`; encode other U+0000-U+001F
   controls as lowercase `\u00xx`. Escape quotes/backslashes as `\"`/`\\`; forward
   slashes remain unescaped. Other Unicode remains unescaped. Unicode normalization
   is not applied; preserve code points. Lone surrogates and unsupported Python values are rejected.
2. Every binary64 real value, including coordinates, weather, features, scores,
   configuration reals and explanation contributions, becomes a JSON **string**:
   convert the exact binary64 value to decimal, round to 17 significant digits
   using round-to-nearest/ties-to-even, then render fixed-point without an exponent.
   Remove trailing fractional zeros and a terminal decimal point. Both signed
   zeros become `"0"`. For example 0.1 becomes `"0.10000000000000001"` and 150.0
   becomes `"150"`. This preserves binary64 round trips without imposing a coarse
   measurement rounding on scores or SHAP values. The decimal context is fixed,
   independent of the caller's ambient context.
3. Integer counts/years remain JSON integers, bounded to the exact interoperable
   range `[-(2^53-1), 2^53-1]`; booleans remain JSON booleans. Null is always JSON
   `null`; missing weather, baseline and probability values never become zero,
   an empty string or an omitted required field. NaN and infinities are rejected.
4. Typed timestamps normalize to UTC and exactly six fractional digits with `Z`:
   `YYYY-MM-DDTHH:MM:SS.ffffffZ`. Naive timestamps/epoch inputs are rejected.
   The same conversion applies to the named timestamp fields inside lineage:
   `as_of`, `scored_at`, `valid_at`, `retrieved_at`, `retrieval_cutoff`,
   `window_start`, `window_end`, `latest_detection_at`, `earliest_snapshot`,
   `precipitation_interval_end`. Version-specific strings that are not timestamps
   remain unchanged. UUIDs use lowercase hyphenated text.
5. Normalize children first, then sort unordered arrays by the Unicode-code-point
   ordering of each element's compact sorted-key JSON. This applies recursively
   to `warnings`, `quality_flags`, `source_asset_ids`, `member_ids`, `batch_ids`,
   `assets`, `satellite_assets`, `member_revisions`, `selected`, `records` and
   `source_checksums`. Sorting does not silently discard duplicates; input
   validators reject ambiguous observation/source identities. Other arrays retain
   order: reasons, feature input order, SHAP paths and reference-year intervals
   are positional. The baseline statistics arrays `source_assets` and
   `source_hashes` are paired in reference-year order and also stay positional.
   This is a FireWatch format, not an RFC 8785 claim.
6. Reject nesting deeper than 64 and serialized payloads over 32 MiB. Changing
   precision, ordering, timestamp rules or encoding requires a new canonicalization
   version. Changing the envelope schema requires a new evidence schema version.

`feature_payload_json` is an intentional opaque string containing the exact legacy
sorted-key JSON preimage of the upstream `features-v1` hash, excluding its own
`feature_hash` member. The hash is SHA-256 over that string's UTF-8 bytes. The
string preserves original float/timestamp representations and array order: do not
recursively rewrite its contents under the new evidence rules. This lets clients
verify the original feature hash without reinterpreting its serialization scheme.
The regular structured `feature` member supplies the normalized readable values.
Other upstream digests keep their original versioned algorithms; the evidence
hash binds those declared identities and the saved normalized provenance.

A fixed UTF-8/UTC/decimal/order vector and SHA-256 are tested independently in
`tests/unit/test_evidence.py`. The committed
[synthetic example](evidence-v1-example.json) is exact canonical JSON without a
trailing newline; it is test data, not an actual fire alert or ML evaluation.

## Persistence, retries and outbox

The run configuration freezes the canonicalization version and region description
at admission. The worker validates the payload and creates its canonical bytes
once, hashes those same bytes with SHA-256, and persists them in
`alerts.canonical_payload` (BYTEA) beside lowercase hexadecimal `original_hash`.
JSONB formatting is never used to recreate the saved digest. Existing PostgreSQL
hash constraints and UPDATE/DELETE/TRUNCATE guards protect the saved bytes.
No database migration or new dependency is required.

Alert inserts, optional anchor-outbox inserts and the evidence-stage checkpoint
share a fenced transaction after the independently committed scoring stage.
An outbox failure rolls back all evidence from that attempt and the checkpoint;
saved scores survive for retry. Deterministic alert and outbox IDs and the unique
alert/network constraint prevent duplicates. A successfully committed checkpoint
is never reserialized on retry. The outbox references the immutable alert whose
exact saved digest will be anchored; it does not calculate a second digest.

The optional destination network is frozen at admission, without RPC calls.
Chain outages cannot block evidence publication. Chain-disabled runs save local
evidence without creating an unusable outbox entry. Trusted portable replays
continue to suppress automatic anchoring. Chain execution is Phase 5.4 work.

## Existing evidence compatibility

Pre-5.1 runs lack `evidence_canonicalization` in their frozen orchestration config.
They resume using the exact previous encoding and payload structure. Completed
runs return existing results without rewriting bytes or hashes. Old records may
also say `evidence-v1`, but lack `canonicalization_version`; they are explicitly
legacy, not examples of `evidence-json-v1`. Never recanonicalize them to compare
their original digests. Consumers distinguish formats by the version pair and
hash the actual saved bytes. The generic legacy `canonical_bytes()` helper stays
unchanged because dataset, replay and ML packages also depend on it.

Normal hotspot/score API responses retain numeric fields. The opaque alert payload
uses decimal strings for this version and remains a public, potentially redacted
projection. Hash verification always uses the saved bytes, never that projection.
Full artifact verification and expanded verification API behavior remain Phase 5.2.
Integrity evidence does not establish that a fire exists or a model is correct.

## Reproduce local acceptance

From the repository root and locked Python 3.12 environment:

```powershell
.venv/Scripts/python.exe -B -m scripts.verify_evidence
.venv/Scripts/python.exe -B scripts/verify_bootstrap.py
```

The evidence smoke uses all three real cached replay bundles with provider HTTP
denied and the baseline scorer, in a freshly migrated disposable test schema.
It verifies exact hashes, upstream feature preimages, repeat-run byte preservation,
empty/degraded cases and absence of live heads/outbox entries. It compares the
application's existing alert bytes/hashes, run/job/score/registry counts and region
enablement before/after, then writes the sanitized
[acceptance receipt](phase-5.1-smoke.json). It neither activates ML nor contacts a
chain, modifies old source/release packages or consumes the final holdout.
