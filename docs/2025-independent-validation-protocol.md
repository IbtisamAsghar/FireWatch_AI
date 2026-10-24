# Independent 2025 validation protocol

Frozen on 24 October 2026 before collecting the seasonal validation cohort or
examining its labels/model scores. A five-day July source-access probe returned
31 observations; no target labels or predictions were inspected for that probe.

## Purpose and fixed boundaries

The original 2024 final holdout was already opened and its original private ledger
and evaluated artifacts are unavailable. It must not be reused for selection or
another release test. This separate versioned design retains the feature/target
definitions and evaluation minimums, and uses May-October 2025 as the new final
holdout. The 2024-08-15 through 2024-11-01 period is outside development blocks.

Configuration is fixed in `configs/target-dataset-2025-holdout-v1.yaml` and
`configs/splits-2025-holdout-v1.yaml`. The original v1 designs remain supported
without changing their allowed seasons. The new version adds the explicit 2025
season; it does not allow arbitrary periods.

Development uses the original pre-15-August-2024 chronological blocks and forward
grouped CV. Parameters are chosen only within those development folds using
`configs/forest-v1.yaml`; nested forward threshold selection and all promotion
gates remain in `configs/evaluation-v1.yaml`. Full-source event grouping,
conservative near-neighbor merging, 48-hour purges and missing-data flags remain.

All 2025 examples are final-test-only. They may be prepared and counted for source
and class sufficiency, but no final predictions/metrics are inspected until the
development model and thresholds are frozen and the one-shot ledger is claimed.
No parameter, threshold, feature, cohort or gate may be changed in response to
the final result. A failed release gate retains baseline scoring.

## Source and weather provenance

Use real NOAA-20 SP area-API responses for northern California, including source
margins from 30 April through 1 November 2025. Preserve every raw response and
checksum; record assembly of any local CSV explicitly. A successful API response
with zero rows means no reported satellite observations, not no fires or assured
cloud-free coverage. No weather or observation may be fabricated. Existing cached
GFS weather can be used when available; missing weather/climate stays flagged.

The weather-enriched published frontend replay remains separate from frozen
training packages. Sparse historical training weather does not establish
weather-sensitive model skill or live NRT generalization.

## Release and review

Use the existing durable final-test ledger and source-bound recomputation. Block
the known consumed 2024 holdout with a clearly labelled recovery entry, retaining
the public receipt as evidence; do not represent that entry as a restored original
ledger. Preserve both this recovery block and the new 2025 claim across machines.

If all fixed release gates pass, produce the compact inference bundle and model
card for actual independent human review. The code must not invent a reviewer,
review rationale or an approval attestation. Promotion, registry activation and
runtime scoring require the normal exact-artifact review contract. Initial scope
can only be historical replay; live approval requires a separate generalization
review. This protocol is an experiment plan, not a performance or approval claim.
