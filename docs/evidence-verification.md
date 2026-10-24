# Evidence verification and audit: Phase 5.2

Verification is read-only. It compares exact saved bytes with their saved SHA-256,
optionally checks the local files referenced by those bytes, and reports chain
comparison separately. It never updates an alert, queues an anchor, signs a
transaction, fetches a source, fits a model or changes the active scorer.

## HTTP interface

```text
GET /api/v1/alerts/<alert-uuid>/verify
GET /api/v1/alerts/<alert-uuid>/verify?verify_artifacts=true
GET /api/v1/alerts/<alert-uuid>?verify_artifacts=true
```

Optional `run_id` enforces the existing run scope. Existing read authentication
policy applies. Unknown query parameters are rejected. The ordinary alert detail
and nested hotspot evidence keep inexpensive payload-only verification by default.
Artifact verification is explicitly opt-in, because it reads files from disk.

`local_status` is `local_match` or `local_mismatch`. `computed_hash` always hashes
the exact original `alerts.canonical_payload`, including its original encoding and
whitespace. It does not hash a redacted public projection, parse-and-reserialize
JSON, or recanonicalize legacy alerts. A corrupt payload does not prevent the
local mismatch from being reported. A mismatch prevents following its artifact
references (`artifact_audit.reason=payload_mismatch`).

## Artifact results

`artifact_verification` and `artifact_audit.status` agree:

| State | Meaning |
|---|---|
| `not_performed` | Artifact checks were not requested; no source/model files were opened. |
| `verified` | Every supported referenced artifact matched its pinned checksum. |
| `mismatch` | At least one recorded identity, file checksum or model manifest disagreed. This takes precedence over unavailable files. |
| `partial` | Some references matched and others could not be checked. |
| `unavailable` | No references could be verified, the payload was unsupported/invalid, or its own hash disagreed. |

Each item returns only `kind` (`source`/`model`), `artifact_id`, `status`
(`match`/`mismatch`/`unavailable`) and a stable reason. Reasons distinguish missing
records/files, unsafe paths, read failures, size limits, metadata mismatches,
checksum mismatches and invalid model manifests. No paths, provider URLs, raw
exceptions, credentials or model contents are returned. Verification failure is a
normal HTTP 200 audit result; bad scope/UUID/query/authentication and unavailable
database errors retain their existing HTTP contracts.

For `evidence-json-v1`, source references come from the saved `source_checksums`.
Their UUIDs resolve to server-owned `source_assets` rows; current metadata must
agree with the checksum pinned in the evidence. Only regular files beneath
`RAW_DATA_DIR` or `REPLAY_BUNDLE_DIR` may be read. Traversal, symbolic links and
Windows junctions in any path component are rejected. No client or payload path
is opened. Missing archived files are unavailable, not evidence of tampering.

Supervised evidence also pins a model UUID, scorer version and inference-manifest
checksum. The model row must agree. The auditor checks the exact manifest bytes
and all three named bundle files (`model.json`, `thresholds.json`,
`feature-contract.json`), including sizes and checksums, inside `MODEL_DIR`.
Unexpected file names and malformed/ambiguous manifest JSON fail verification.
It never imports or deserializes an estimator. Dependency versions, model approval
and current activation are not requirements for historical byte-integrity checks;
retired models remain auditable if their original files are retained.

Legacy worker payloads can audit their frozen enrichment `assets` and
`satellite_assets` pins. Legacy supervised payloads without a pinned inference
manifest, unrecognized encodings and unrelated synthetic payloads report
`unavailable`, never fabricated success. Consistent repeated source references are
deduplicated; conflicting references are rejected.

Bounds per request: 32 MiB evidence JSON, 256 references, 64 MiB per source file,
64 KiB model manifest, 32 MiB per model member, and 256 MiB total file reads.
Reads enforce actual byte limits as well as file sizes. Files are rechecked on
each audit; a previous match is not reused after disk changes. These are direct
referenced-file checks, not recursive provenance reconstruction or proof that
normalized measurements, training, calibration or scientific conclusions are valid.

## Anchor states and limitations

`anchor_status` is independent of the local/artifact results:

- `unanchored`: no saved anchor or pending outbox entry.
- `anchor_pending`: queued/submitted delivery or an unconfirmed receipt.
- `anchor_match`: a trusted read-only adapter retrieved matching digests for all
  recorded confirmed destinations.
- `anchor_mismatch`: a successful read returned a valid different digest.
- `anchor_unavailable`: adapter disabled/absent, failed receipt, malformed response
  or RPC failure. An outage is not a mismatch.

Stored receipt digests alone never establish `anchor_match`. The adapter receives
the recorded chain/contract identity. No production RPC adapter is supplied by
this phase; that belongs to the optional chain work. The CLI has no adapter and
honestly reports confirmed chain comparisons as unavailable. Local-chain records
remain labeled by `network_kind` and do not constitute independent public
notarization. Tests use injected read-only adapters for match/mismatch/outage cases.

An attacker controlling both the payload and stored digest can rewrite both. A
matching independently anchored digest supplies a separate comparison point.
Integrity verification does not prove a fire is real or a model is correct.

## CLI and safe tamper demonstration

```powershell
firewatch verify-alert --alert-id <alert-uuid>
firewatch verify-alert --alert-id <alert-uuid> --artifacts
firewatch verify-alert --alert-id <alert-uuid> --payload-copy <copied-payload.json>
```

The CLI starts a PostgreSQL read-only transaction and prints the same verification
contract. `--payload-copy` adds a separate `copied_payload` result, comparing the
copy's exact bytes with the saved original digest. It never replaces database
evidence. A redacted API projection is not an original-byte export and should not
be expected to match. Use a separately retained original or a synthetic fixture.

Exit codes: 0 for a successful requested check, 1 for a detected local/artifact/copy
mismatch, and 2 for invalid input, unavailable database/copy, or incomplete
requested artifact verification. An unrequested chain comparison does not change
the CLI exit code; inspect `anchor_status` separately.

A standalone synthetic demonstration needs no database or secrets:

```powershell
@'
import json
from pathlib import Path
from firewatch.evidence.canonical import evidence_hash
from firewatch.evidence.verify import verify_payload
original = Path("docs/evidence-v1-example.json").read_bytes()
expected = evidence_hash(original)
copied = json.loads(original)
copied["priority_score"] = "999"
changed = json.dumps(copied).encode()
print("original:", verify_payload(original, expected)[0])
print("changed copy:", verify_payload(changed, expected)[0])
assert evidence_hash(original) == expected
'@ | .\.venv\Scripts\python.exe -
```

Expected: `local_match` for the original and `local_mismatch` for the changed copy.
No immutable real alert is modified.

See [Phase 5.2 acceptance](phase-5.2-checklist.md) and
[canonical encoding](canonical-evidence.md).
