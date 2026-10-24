# FireWatch scorer and candidate model card

Current local release preparation, 24 October 2026: the new candidate
`forest-independent-2025-v2` passed the fixed ranking gates on a separately
reserved 2025 final holdout and was approved by Talha for historical replay.
It is now registry-active. Its exact report, artifact pins and review are described in
[the current review package](independent-2025-model-review.md).
Real weather/climate-enriched ML and baseline hotspot results are available through
[the local readiness guide](local-real-readiness.md). The trained candidate
is approved only for historical northern-California replay. Metrics below describe the older 2024 release.

Local recovery, 24 October 2026: a new real-data development forest is now saved
in this checkout. See [the recovery record](recovered-model-training.md).
The historical final metrics below apply to the original release candidate,
not to this newly fitted artifact. Neither candidate is approved or active.

## Historical 2024 candidate record

The following sections retain the earlier candidate's historical state and metrics.
They do not describe the current `forest-independent-2025-v2` activation above.

Release state: **ranking candidate passed final evaluation; independent approval pending**.
The default scorer remains `baseline-v1`; no ML model is approved or active.
Historical backfill fixed the zero-eligible-row blocker. The frozen one-shot
final evaluation now supports a historical ranking candidate. Calibrated
probability remains unsupported and null. See the
[ranking release handoff](ranking-ml-release.md) for exact evidence and review inputs.

## Intended use and scope

Research prioritization of satellite hotspots for analyst inspection. The target
is later satellite-observed FRP strengthening within 24 hours and 2 km of the
snapshot centroid. Later FRP must strictly exceed current maximum FRP plus
max(5 MW, 20% of current maximum). The target is an observation proxy, not
physical severity, burned area, confirmed ignition, spread or emergency advice.

Historical cohort design covers northern California (west -122.5, south 38.5,
east -120, north 40.5), NOAA-20 SP, May–October in 2022 and 2024. The seasonal backfill now materializes
cadence-aligned snapshots across that design. NRT and NOAA-21 products
require separately reviewed cohorts; historical SP results would not establish
live generalization.

## Data and training state

The original diagnostic dataset contained 12 off-cadence snapshots and zero
eligible rows. The new historical backfill processes 1,472 six-hour UTC cutoffs,
producing 1,975 snapshots: 157 positive, 1,662 negative and 156 unknown labels.
All 1,819 known-label rows are eligible before event splitting and purging.

The unchanged split policy accepts training and retains a usable final holdout.
Its calibration period has only four positives, so the existing grouped temporal
cross-validation fallback is selected. The retained roles contain 807 train,
181 validation, 72 calibration, 180 threshold and 440 final-test rows.
Rows removed by purge/event/target rules do not enter those partitions.

Weather temperature is missing in 1,969 snapshots. Missing measurements remain
null with explicit flags; this is a satellite-feature dataset with sparse weather,
not a fully weather-enriched or operationally validated model. A bounded weather
fill workflow is available. Later weather retrievals require a new frozen backfill
job and downstream experiment artifacts.

A real Random Forest candidate has now been fitted and exported with deterministic
source-bound recomputation and portable prediction parity. The original training
selection has 32 trees, maximum depth 8, minimum leaf size 1 and no class weighting.
That original package remains `fitted_development_only`. Nested forward
development evaluation selected a replacement forest with 64 trees, depth 8,
minimum leaf size 3 and balanced class weights. It remains uncalibrated with
`probability_supported=false`, unregistered and inactive.

The final-test ledger is now committed for the frozen release experiment.
On 440 held-out rows (33 positive, 407 negative, 170 thermal partition groups),
forest average precision is 0.190205 versus 0.0529941 and 0.0622052 for the two
baselines. Paired whole-event 95% AP-improvement intervals are strictly positive
against both baselines, with 200 valid replicates. At the selected High boundary,
precision is 0.12 and recall is 0.636364: false positives remain substantial.
AP is a ranking measure, not accuracy or a calibrated hotspot probability.

See the [ranking release receipt](ranking-release-receipt.json) for exact metrics
and artifact pins. The [historical backfill workflow](historical-training-backfill.md)
and [original backfill receipt](training-backfill-receipt.json) retain their earlier
training-stage evidence. Original Phase 4.8 baseline evidence remains frozen.

## Baselines, thresholds and explanations

`baseline-v1` ranks current maximum FRP in MW. Provisional priority boundaries are
10/50/100 MW for Medium/High/Critical. `weighted-baseline-v1` is an explicit
alternative: 40% intensity, 25% dryness, 20% wind and 15% previous detections,
with provisional index boundaries 35/55/75. Both return
`escalation_probability: null`; neither scale is a probability. Missing inputs
retain explicit flags and policy-specific missing floors. See the frozen
[baseline policy](../configs/baselines-v1.yaml) and [baseline guide](baselines-and-comparison.md).

Baseline reasons identify measured contributors and missing inputs. Future
approved forests use exact tree-path-dependent SHAP contributions in raw forest
score units; these do not sum to calibrated probability. Raw uncalibrated ML
scores also have null probability.

## Bias, failure cases and restrictions

Clouds, smoke, overpass timing, sensor coverage and gaps can hide later activity.
A negative observation label does not confirm absence of fire or strengthening.
Incomplete follow-up remains unknown. Compact-cluster and source-footprint gates
exclude unsupported target geometry. Weather availability and product/region
differences constrain transfer. Thermal event groups are partition proxies,
not independently verified incidents; repeated snapshots are not independent fires.

Priority labels are provisional research categories. Missing required FRP
withholds ML scoring. An unavailable or invalid approved model fails explicitly.
Historical saved results retain their scorer and approval pins across promotion
and rollback.

## Reproduction and future approval

Follow [Phase 4.8 acceptance and handoff](phase-4.8-checklist.md) for export,
independent verification and reproduction using locked dependencies. Preserve
the release manifest SHA-256 through a separately trusted channel. A checksum
alone is an integrity check, not an authenticated review or a performance claim.

An approved ML bundle is still unavailable. Score/threshold selection and the
frozen one-shot final evaluation have completed with positive paired baseline
improvements. The exact inference candidate and review hashes are recorded in
the [ranking release handoff](ranking-ml-release.md). Independent limitations
review and registry promotion remain required before activation. These historical
SP results do not establish live generalization. Follow
[registry promotion](model-registry-and-promotion.md); no bypass is supplied.
