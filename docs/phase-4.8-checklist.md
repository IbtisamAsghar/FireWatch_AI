# Phase 4.8 acceptance and handoff

Deliverable: **documented baseline release**, with explanations and verified
evaluation evidence. No approved supervised model exists. Phase 4 engineering
acceptance does not establish scientific model readiness.

## Acceptance evidence

- [x] Exact label boundaries and incomplete follow-up:
  `tests/unit/test_target_labels.py` covers strict FRP increase, open T / closed
  T+24h, closed 2 km radius, missing FRP, archive gaps and provider outages.
- [x] Future records cannot affect features at T:
  `test_future_records_and_exact_24h_boundary_do_not_change_features` in
  `tests/unit/test_feature_processing.py` covers future satellite/weather records;
  `test_future_labels_do_not_mutate_current_features_or_hash` in
  `tests/unit/test_target_labels.py` proves labels cannot rewrite feature bytes.
- [x] Event disjointness and purge:
  `tests/unit/test_temporal_splits.py` proves five disjoint roles, closed ±48-hour
  boundaries, removal of whole cross-boundary events, and forward CV isolation.
- [x] Serialization parity:
  `test_model_json_roundtrip_and_repeatability` in
  `tests/unit/test_forest_training.py` compares full predictions before/after JSON;
  `tests/unit/test_inference.py` compares approved-bundle batch results against
  in-memory calibrated predictions and verifies raw-score SHAP additivity.
- [x] Null probability at API boundaries:
  `test_baseline_and_uncalibrated_api_never_claim_probability` in
  `tests/integration/test_read_api.py` checks both baselines and uncalibrated
  model scores across list/detail/GeoJSON. Synthetic records use disposable
  PostgreSQL schemas and establish no real model approval.
- [x] Model card, training manifest, evaluation report and reproducible commands:
  portable baseline package retains all source-bound evidence. The
  `approved_bundle` field is explicitly null with a reason. An approved bundle
  is inapplicable to this baseline release; future supervised releases retain
  their reviewed bundle through Phase 4.7.

The baseline package binds the evaluation and baseline comparison to the same
split manifest. Export checks both complete upstream provenance chains before
copying; verification recomputes those chains and regenerates the release claims,
card and commands. It rejects mismatched splits, fitted/final source evidence,
altered hashes/claims, path traversal, unexpected files and unsafe output locations.
Output is staged and atomically renamed; existing directories are never overwritten.
No database, provider, registry approval, activation or final-test ledger access is
part of export or verification.

## Real release and operator commands

Use the repository root and the installed locked Python 3.12 environment:

```powershell
.venv/Scripts/python.exe -m scripts.verify_acceptance
```

This creates the private `data/handoffs/module-4-v1` package if absent, verifies it
and writes the public [acceptance receipt](phase-4.8-smoke.json). The receipt
contains reviewed source hashes, release manifest SHA-256, counts and the explicit
absence of real ML metrics/approval. Its reviewed real-source constants must not
be silently changed to bless new data.

Equivalent generic export:

```powershell
firewatch export-baseline-release --evaluation data/evaluations/phase-4.5-final --evaluation-sha256 4e002768fc7a1b73e0331587e6c44ae3eb70890840e6883a60072473166aa12a --comparison data/baselines/phase-4.3-final --comparison-sha256 2d50d21aa2d7ab4585a512f7a1e080f7e496ea65526dfc4eb5550da77e4ec180 --output data/handoffs/module-4-v1
$receipt = Get-Content docs/phase-4.8-smoke.json | ConvertFrom-Json
firewatch verify-baseline-release --path data/handoffs/module-4-v1 --manifest-sha256 $receipt.manifest_sha256
```

Share the complete package only with authorized recipients; it includes historical
source observations. Preserve the manifest SHA-256 through a separately trusted
channel. Git contains the model card, commands, tests and count/hash receipt,
while private data stays ignored.

After relocation, use `REPRODUCE.md` inside the package. It verifies the original
evidence and reproduces training/development evaluation with the packaged frozen
policies. Fresh outputs are siblings of the package so the verified release stays
immutable. The commands do not open the final test or attempt promotion.

## Data readiness and next steps

The reviewed real package has 12 off-cadence diagnostic snapshots and zero eligible
supervised rows. No forest/calibrator, ML thresholds, held-out ML metrics, approved
bundle or live-generalization claim is supplied. Baseline selection remains
explicit; no runtime setting changes as part of acceptance.

Continue collecting cadence-aligned historical feature snapshots and complete
24-hour follow-up in the frozen source cohort. Obtain sufficient positive/negative
events across disjoint periods before training, calibration and final evaluation.
For a future eligible model use the source-bound independent review and promotion
workflow in [the registry guide](model-registry-and-promotion.md).

## Verification results

The full PostgreSQL-backed regression passed **1,072 tests** in 438.99 seconds.
The final early-source refusal guard was added during that run; its eight-test
CLI/trust suite passed separately, including the two new final/fitted-source
refusal cases. The complete final-code Phase 4.8 suite passed all 22 new cases in 43.83 seconds.

Ruff lint/format, strict mypy (97 source files), scaffold/local links, unchanged
hashed locks (54 runtime / 71 development packages), pip consistency and
source/wheel builds passed. Configured-credential scanning passed 349 nonignored
files; the public unauthenticated loopback chain-RPC default was excluded from
credential matching. The private handoff and test output remain Git-ignored.

The installed wheel verified both CLI help commands and the actual 43-file
baseline package from an isolated directory without .env. Settings, provider
network, database connections, pickle loading and Random Forest fitting were
denied during that check. The trusted release digest is recorded in the public
receipt; all original upstream evaluation/comparison hashes remain unchanged.

One existing Starlette/httpx TestClient deprecation warning remains. Hosted CI,
real ML performance and independent model approval are not asserted by these
local checks.
