# FireWatch current model card

## Release and approval

The delivered managed release is `forest-independent-2025-v2`. Its recorded
independent review by Talha permits historical northern-California replay only.
The source database was registry-active when exported; a fresh clone has no
serving state until it is restored and configured. A clean empty installation
uses `baseline-v1` by default. See the [review package](independent-2025-model-review.md)
and [installation/restore guide](local-real-readiness.md).

The managed release, its review, frozen evaluation, inference bundle and final-test
ledger remain immutable. Older 2024 metrics and recovery-stage claims belong to
[the historical ranking release](ranking-ml-release.md) and
[the recovery record](recovered-model-training.md); they do not describe this release.

## Target and intended use

Research ranking of satellite hotspot clusters for analyst inspection. The target
is later satellite-observed FRP strengthening within 24 hours and 2 km, strictly
exceeding current maximum FRP plus max(5 MW, 20% of current maximum).
This is an observation proxy, not confirmed ignition, burned area, physical
severity or emergency advice. Scope is the evaluated northern-California box:
west -122.5, south 38.5, east -120, north 40.5; historical NOAA-20 SP replay.
Live NRT, NOAA-21 and Pakistan/new-region use require separate validation/review.

## Frozen 2025 final evaluation

The reserved May–October 2025 holdout has 811 rows, 47 positive and 764 negative,
across 438 thermal partition groups. These groups are satellite proxies, not
independently verified incidents. Average precision (AP, not accuracy) is:

- Forest: 0.109701.
- Maximum-FRP baseline: 0.050746.
- Weighted baseline: 0.055594.
- Logistic comparison: 0.139349.
- Forest without climate: 0.108541.
- Forest without weather/climate: 0.111370.

Paired whole-event 95% forest AP-improvement intervals are [0.020698, 0.130192]
and [0.008255, 0.124696] against the two baselines, with 200 valid bootstrap
replicates each. The project baseline ranking gates passed. Forest superiority
over logistic was not established. At the saved High boundary, forest precision
is 0.094488 and recall 0.510638: 24 true positives, 230 false positives,
23 false negatives and 534 true negatives.

The final holdout is consumed. Do not retune or select a replacement on these
results. Preserve the one-shot ledger, including its recovery block for the
previously consumed 2024 period. Verification may audit the frozen experiment;
it must not create a new final-test claim.

## Runtime model and enrichment

The JSON forest has 64 trees, maximum depth 8, minimum leaf size 1 and balanced
class weights. Calibration is `cv_uncalibrated`; `probability_supported=false`.
`model_score` is a ranking value and `escalation_probability` stays null.
Low/Medium/High/Critical are provisional research categories, not physical severity.

The shared contract has 17 values and 14 missing indicators. All non-leaf splits
in this delivered forest use nine satellite/activity features; none uses weather,
climate or missing indicators. The enriched six-hotspot demonstration includes
real weather/climate context, but that does not establish enrichment-sensitive
model skill. Additional weather/climate skill is not established by ablations.

Preprocessing is frozen from training data. Missing inputs retain explicit flags;
missing required FRP withholds ML scoring. Exact tree-path-dependent SHAP explains
the raw model score, not calibrated probability. Artifact or approval errors fail
explicitly without silent baseline substitution.

## Limitations and reproduction

Clouds, smoke, overpass timing, coverage gaps and unobserved negatives constrain
interpretation. Repeated snapshots are not independent fires. Historical SP
results do not establish live generalization. These data and scores must not be
used for emergency response or protection of life/property.

Use [the frozen final report/card](../data/evaluations/independent-2025-final-v2/model-card.md)
and [recorded review](independent-2025-model-review.md) for exact artifact pins.
Checksums bind bytes to a trusted reference; they do not prove incident truth or
authenticate a reviewer by themselves. No model was retrained or newly approved
as part of delivery portability repairs.
