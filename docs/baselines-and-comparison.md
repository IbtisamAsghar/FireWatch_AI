# Baselines and the simple supervised comparison (Phase 4.3)

Both baseline scorers run directly on validated `features-v1` snapshots with no
model file, database model registration, fit step, network access or provider
credentials. They emit `priority_score`, `score_kind=baseline`, and
`escalation_probability=null`. The default remains `baseline-v1`. Weighted scoring
is opt-in by setting `ACTIVE_SCORER=weighted-baseline-v1` for subsequent jobs;
queued jobs and completed run evidence retain their pinned scorer versions.
The worker and `/api/v1/models/current` recognize both built-ins. There is no
logistic model loader or training endpoint in the deployed API.

## A: maximum-FRP ranking

`baseline-v1` returns the snapshot's maximum FRP in MW, with a zero floor when
FRP is missing. Existing display boundaries 10/50/100 MW and the original reasons
and explanation payload remain unchanged, preserving earlier evidence behavior.
Ranking is solely by maximum FRP; ties can use snapshot ID or frozen comparison
row ID. This is an observed intensity ranking, not a probability or physical fire
severity estimate. Display categories are provisional research conventions.

## B: weighted index

`weighted-baseline-v1` uses a frozen weighted index on a 0–100 scale:

- Intensity (40%): `min(1, log1p(frp_max_mw) / log1p(frp_reference_mw))`.
- Dryness (25%): `1 - humidity_pct / 100`.
- Wind (20%): `min(1, wind_ms / wind_reference_ms)`.
- Previous detections (15%):
  `min(1, log1p(detections_24h - detections_6h) / log1p(previous_reference))`.

Previous detections cover `(T-24h,T-6h]` in the existing spatial neighborhood;
current activity is excluded from that term. All inputs are past-only features.
Each unavailable term contributes zero using its original fixed weight. Weights
are never redistributed to available terms. Missing components and weighted
contributions are explicit in the explanation. A low incomplete score must not
be interpreted as evidence of low physical danger. Incomplete satellite coverage
can also hide past detections even when the count is numerically zero.

The artifact-free prototype uses FRP=100 MW, wind=15 m/s, and previous-count=20
reference scales. The 0.40/0.25/0.20/0.15 weights are prespecified, unoptimized
prototype assumptions. Neither weights nor scales are claimed to be validated by
the sparse real cohort. The frozen catalog is `configs/baselines-v1.yaml`; a test
binds it to the built-in implementation. Weight changes require a reviewed policy
version. A policy SHA identifies all scales, weights, fit rows and fit basis.

When development data is sufficient, the offline comparison freezes positive
reference scales from training-feature 90th percentiles, floored at one (all-missing
FRP/wind use the prespecified scales). Weights remain fixed before label evaluation.
This uses training data only, including in each expanding CV fold. These
source-bound development scales are saved in the comparison; they do not alter
the deployed prototype. Promotion would require a separately reviewed scorer
version. Validation, calibration, threshold and final-test data never fit scales.

Prototype display boundaries are 35/55/75 on the index scale. They are explicitly
provisional and have no claimed relationship to the FRP baseline's MW boundaries.
Phase 4.5 will select supported thresholds from development evidence. A rule index
is never named `escalation_probability`, even though it is bounded.

## Logistic comparison and paired evaluation

Sufficient Phase 4.2 chronological splits use training for fitting and validation
as the common held-out cohort. Sparse grouped temporal CV uses each sufficient
fold's earlier training rows and later validation rows. Every method receives the
same ordered row identities, source-bound labels, and frozen features; missing
measurements do not cause method-specific row dropping. Unknown labels and
excluded rows do not enter evaluation. Final-test rows are inaccessible through
the development selector. Chronological comparisons also leave calibration and threshold blocks untouched.
Forward CV reassigns development rows by fold; original calibration/threshold
rows may enter fold training or validation. Independent calibration/threshold
procedures therefore require a new reserved cohort or nested cross-fitting in
CV mode. Final release evaluation remains sealed for Phase 4.5.

The fixed scikit-learn Pipeline uses median imputation, scaling of 17 measurements,
14 existing missing flags, and logistic regression (`C=1`, `lbfgs`, `max_iter=1000`,
`tol=1e-6`, seed 42, no class weighting). All-missing columns retain their positions
with zero imputation and their missing flags. Fit requires at least three training
events and five rows of each class, in addition to the split sufficiency gates.
Nonconvergence fails closed. No hyperparameter search, random split, calibration,
Random Forest, final-test selection, or model promotion occurs here.

Logistic outputs are signed decision margins named `model_score`; they are not
exposed as calibrated escalation probabilities. The report records exact fit IDs,
ordered feature names, coefficients, intercept, imputation/scaling statistics,
fixed parameters, dependency versions and dataset/checksum bindings. There is no
pickle/joblib loading. This comparison is an offline research path and does not
make the fitted estimator available to API inference.

For each common validation cohort the report saves paired scores, class counts,
prevalence, average precision (AP), ROC AUC, and mean per-run precision@10 and
recall@10. Use `k=min(10,n)`, stable row-ID tie breaking, and omit zero-positive runs
from recall aggregation while reporting their count. AP is the scikit-learn
average-precision statistic, not trapezoidal PR area. Undefined single-class
metrics are null. Repeated snapshots are not independent fires; no confidence
interval or generalization claim is inferred from these development metrics.
Full threshold, sensitivity, uncertainty and final release analysis is Phase 4.5.

Implementation follows official [pipeline leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html)
and [logistic decision-function documentation](https://scikit-learn.org/stable/modules/generated/sklearn.linear_model.LogisticRegression.html).

## Offline comparison workflow

Run from the repository root in the installed environment:

```powershell
firewatch compare-baselines --splits data/splits/phase-4.2-final --manifest-sha256 cca85bb36cc4a207a152b38686f685de961a8b6ebb9c9ddaa807547d17d08264 --output data/baselines/new-comparison
firewatch verify-baseline-comparison --path data/baselines/phase-4.3-final --manifest-sha256 2d50d21aa2d7ab4585a512f7a1e080f7e496ea65526dfc4eb5550da77e4ec180
python -m scripts.verify_baselines
```

The output must be a new directory. Export verifies the source split package,
creates a private atomic staging directory, copies source-bound split/label inputs,
saves the comparison and hashed inventory, verifies, and publishes. A failed export
cleans only its own staging directory. Verification is relocatable, credential-free,
and recomputes source labels, event groups, partitions, preprocessing, fits and
metrics. NumPy/scikit-learn versions must match those bound in the manifest. Tiny
native numerical roundoff is tolerated in recomputed report floats; byte hashes
and row identities are exact. Always pin the trusted manifest hash for transfer
verification; self-consistency alone does not authenticate an artifact.

The actual package `data/baselines/phase-4.3-final` reports `insufficient_data`, zero
validation cohorts, and `skipped_insufficient_data` for logistic fitting. Its twelve
real diagnostic snapshots are all off cadence. Both built-in baselines score
those features in memory for diagnostic smoke, without modifying frozen features
or presenting their priorities as held-out metrics. The final test stays sealed.
See [real receipt](phase-4.3-smoke.json) and [acceptance](phase-4.3-checklist.md).
