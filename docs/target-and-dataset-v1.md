# Phase 4.1: observed FRP strengthening and dataset design

## Frozen research question

For a snapshot at UTC cutoff T, will any valid later NOAA-20 SP detection within
2 km of its fixed current centroid have FRP strictly greater than
`current_frp_max + max(5 MW, 0.20 * current_frp_max)` during `(T,T+24h]`?

The target is `observed-frp-strengthening-v1`. [Target configuration](../configs/target-v1.yaml)
and [JSON schema](target-v1.schema.json) freeze the radius, horizon, strict threshold,
primary sensor/product, input eligibility, retrospective interpretation and cadence.
Its SHA-256 is `30e9f3b1ea4c12b7af465c959c4c97140aeb0de171ce6c9b894ef69c3c23393f`.
Changing those values under v1 is rejected. A future change needs an explicitly
reviewed target/design version using development data; final-test results must
never select these prototype settings.

A current 10 MW baseline needs more than 15 MW; 15 MW is negative, 15.0001 MW can
be positive. At 100 MW, more than 120 MW is needed. Real zero is a valid baseline.
Decimal threshold arithmetic avoids a binary-float equality artifact. Haversine
uses the project's fixed Earth radius and a closed 2 km distance comparison,
without inflating the boundary. The centroid stays fixed during follow-up.

This is a satellite-observation proxy, not confirmed wildfire, physical severity,
extinction, continuous monitoring, an emergency rating or a validated probability.
The scope includes the normalizer's thermal anomalies; provider type/confidence
metadata does not turn them into ground-truth incidents.

## Label decisions and unknowns

The pure `label_snapshot` service accepts validated current ClusterDetails and
FeatureSet plus separately pinned future evidence. It never updates current
features, runs, scores or alerts.

- Positive: complete spatial/temporal source coverage, eligible current input and
  at least one valid stronger detection. Missing FRP in another future detection
  does not erase an already observed valid positive.
- Negative: complete coverage and no qualifying stronger detection. If later nearby
  detections exist but no valid positive and at least one FRP is missing, the
  label is unknown rather than a false negative.
- `unobserved_followup=true`: negative with no later nearby detections. This means
  not observed to strengthen; it never means confirmed extinguished.
- `unobserved_followup=false`: observed negative or positive.
- Unknown/null target: incomplete end-of-archive follow-up, missing/rejected/failed
  source days, known provider outage, missing or partial current FRP, spatially wide
  current cluster, unsupported baseline product/sensor or insufficient source bbox.
  Unknown follow-up flags are null. Invalid/corrupted schemas or revision hashes
  fail validation rather than being disguised as a scientifically unknown row.

The full 2 km disk must be inside the downloaded bbox, including latitude-dependent
longitude extent. Otherwise missing observations outside the regional extract could
produce a false negative. The compact-input policy excludes any cluster member
outside the disk and any partial FRP baseline; features-v1 and saved results retain
their existing definitions. Excluding wide components changes the study population:
results cannot automatically generalize to large connected components.

Source days include both dates touched by the open-start/closed-end 24-hour window.
An exact next-day midnight endpoint requires the next calendar day's source too.
A successful source fetched before the end of that day cannot establish full-day
follow-up. Known outage intervals are half-open; an outage starting at the inclusive
label endpoint intersects the target window. [Outage catalog](../configs/label-outages.yaml)
records evidenced incidents only. An empty catalog means none were supplied, not
that an external outage inventory was proved complete.

## Source revisions and availability

Follow-up selection uses one primary product: `VIIRS_NOAA20_SP`. It records successful
calendar-day batch IDs, raw source hashes, retrieval timestamps, region/bbox and
RevisionPolicy. Corrections follow the existing deterministic source-revision policy.
A newer failed/rejected day blocks coverage rather than falling back to an older
clean-looking response. A newer complete empty response replaces earlier activity.

Selection runs in a repeatable-read, read-only PostgreSQL transaction. Acquisition
time is distinct from retrieval time. Labels use a separately declared retrieval
cutoff; they do not reuse a feature's historical availability cutoff or change its
pins. This is retrospective research using later retrieved SP archives. It is not
an operational issue-time backtest. Coverage attests an archive delivery, not
cloud-free satellite observations or continuous sensor availability.

## Dataset design and six-hour cadence

[Dataset design](../configs/target-dataset-v1.yaml) is bound to the target hash and
freezes northern California, the existing region bbox, features-v1's 24-hour
lookback, the fixed 2010-2019 climate reference, and two half-open study periods:
1 May to 1 November in 2022 and 2024. The preserved archives extend from 30 April
through 1 November inclusive, supplying lookback/follow-up margins.

UTC training cutoffs are 00:00, 06:00, 12:00 and 18:00. Each season contains 736
candidate cutoffs; the plan has 1,472 cutoffs in total. These counts are candidate
times, not generated feature rows, labeled fires, independent events or guarantees
of sufficient positive classes. Empty snapshots contribute no training examples.
Seasonal weather/climate enrichment must be materialized and its missingness
reported when generating the training cohort.

The current handoff is a diagnostic cohort. Its 22:00/23:00 active/degraded examples
are off cadence and are excluded from training even when their target label is
known. Off-cadence exclusion is separate from target truth, so diagnostic labels
remain inspectable. Out-of-period and wrong-region inputs are also excluded.

Only the existing ordered 31 inputs may enter a model: 17 values plus 14 missing
indicators. Labels, later detections, feature hashes, coordinates, IDs, timestamps,
sensor/product cohort metadata, event groups, score and partition status are not
model inputs. Future evidence lives in separate files and joins by snapshot and
feature hash. Do not use old baseline priorities as supervised target labels.

No partitions, event IDs, imputation, fitted preprocessing, model or promotion are
created here. Phase 4.2 owns event grouping, chronological partitions and at least
48-hour boundary purging. Training readiness remains false until class sufficiency,
reviewed event independence, complete follow-up and those split checks pass.

## Observation bias and required sensitivity analysis

Clouds, smoke, overpass timing, detection thresholds and changing observations can
hide strengthening. Full delivery does not remove that bias. The release evaluation
must report negative cohorts separately: those with later detections and those
without (`unobserved_followup=true`). Report the all-negative analysis and a
sensitivity analysis omitting unobserved negatives, with cohort/class/event counts.
If a sensitivity cohort has no positives or no negatives, mark its metrics undefined;
do not silently substitute zero. Also stratify by month, event, sensor/product and
weather completeness, as specified by the later evaluation phases.

## Measured sensor/product and overpass diagnostics

[Real-data receipt](phase-4.1-smoke.json) profiles checksum-verified sources, selecting
one revision per source observation. It has 5,496 NOAA-20 SP observations in 2022 and
12,366 in 2024. Their median FRP values are 6.28 and 5.5 MW; their mean FRP values
are approximately 17.47 and 26.52 MW. These different seasons cannot establish a
sensor calibration effect.

The separate 2026 NRT sample has 45 NOAA-20 and 40 NOAA-21 observations. Median FRP
is 7.46 versus 9.535 MW; high-confidence counts are 2 versus 1. NOAA-21 detections
are all daytime at UTC hour 20; NOAA-20 spans UTC hours 09/10/20/21 and includes four
night detections. No detections match across sensors within both 2 km and 30 minutes
in this small sample. That is a descriptive coincidence test, not evidence of sensor
failure or permission to pool the observations.

The receipt includes full UTC acquisition-hour histograms and counts of timestamps
with positive detections. These are not complete overpass inventories: empty and
cloud-obscured passes are unobserved in hotspot CSVs. NOAA-21 SP seasonal inputs are
not imported here; the initial target therefore stays NOAA-20 SP. NRT-to-SP transfer
and NOAA-21 pooling require matched-time/spatial review and separate evaluation.

Official references checked for this phase:
[NASA VIIRS product description](https://firms.modaps.eosdis.nasa.gov/content/descriptions/FIRMS_VIIRS_Firehotspots.html),
[NASA source availability](https://firms.modaps.eosdis.nasa.gov/content/active_fire/),
[NOAA VIIRS limitations](https://vlab.noaa.gov/web/towr-s/viirs-active-fires).
NASA lists NOAA-21 data from January 2024, so it is not a same-sensor replacement
for the 2022 primary cohort. NOAA documents cloud/smoke obscuration and limited
polar-orbiting observation frequency. Neither source validates this prototype target.

## Commands and portable package

```powershell
.\.venv\Scripts\firewatch.exe plan-target-dataset
.\.venv\Scripts\firewatch.exe label-targets --handoff data/handoffs/module-3-v1 --handoff-sha256 6395e9ff7177c1a4c1eec3bbc708f84bbe90c293838b0ccb841a66a0865ce5c7 --output data/labels/new-package --retrieval-cutoff 2026-10-22T12:00:00Z
.\.venv\Scripts\firewatch.exe verify-target-labels --path data/labels/new-package --manifest-sha256 <reviewed-label-manifest-sha256>
.\.venv\Scripts\python.exe -B -m scripts.verify_target_labels
```

Planning and label verification require no settings, database or provider access.
`label-targets` requires the existing local database and verified handoff; it reads
cached historical sources and never calls a provider. The output must be new.
Partial staging output is removed on failure; an existing directory is never replaced.

The private package contains frozen target/design JSON, current input references,
labels.jsonl, separately pinned follow-up files, a diagnostic report and checksummed
manifest. Verification checks paths, sizes, inventories, feature/revision hashes,
source/cutoff/target bindings and recomputes every label. Preserve a reviewed manifest
hash when transferring it. SHA-256 detects changes; it is not external authenticity.
Downloaded rows and full private evidence stay Git-ignored under data/.

The current real package is `data/labels/phase-4.1-final`. All twelve labels
recompute offline: eight unobserved negatives and four unknown wide-cluster rows.
All twelve are excluded from training as off cadence. There are no positive examples
in this tiny diagnostic package; it cannot establish label balance or model skill.
See [acceptance checklist](phase-4.1-checklist.md).
