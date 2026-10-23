# FireWatch scorer and candidate model card

Release state: **documented baseline release; no approved ML model**.
The default scorer remains `baseline-v1`. No held-out ML performance or calibrated
probability can be claimed for the current real data. Phase 4 implements the full
training, evaluation, inference and promotion workflow. Historical backfill now
fixes the zero-eligible-row blocker; final evaluation and independent approval
remain required before ML activation.

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
source-bound recomputation and portable prediction parity. The selected candidate
has 32 trees, maximum depth 8, minimum leaf size 1 and no class weighting.
Its status is `fitted_development_only`; forward CV leaves it uncalibrated and
`probability_supported=false`. It is not registered or active.

The final-test ledger remains unconsumed; no approved ML bundle or supported
final-held-out performance claim exists. See the
[historical backfill workflow](historical-training-backfill.md) and
[real-data receipt](training-backfill-receipt.json) for reproduction,
data limitations and the current offline-candidate state. Original Phase 4.8
baseline evidence remains frozen in its original package.

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

An approved ML bundle is explicitly unavailable in this baseline release.
Eligible historical features/labels and reviewed purged splits now exist.
Future activation still requires supported score/threshold validation, frozen
one-shot final evaluation with positive paired baseline improvements, source-bound
bundle verification and independent limitations review. Then follow
[registry promotion](model-registry-and-promotion.md). No bypass is supplied.
