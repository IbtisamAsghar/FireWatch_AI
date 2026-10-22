# Random Forest selection, calibration and safe artifacts (Phase 4.4)

This phase implements an offline candidate-training workflow. It does not register
or promote a model, add training to the API, select release priority thresholds,
or evaluate the final test. The deployed worker still supports only the two
artifact-free baselines. Real fitting remains conditional on the source-bound
Phase 4.2 development data being sufficient.

## Bounded model selection

`configs/forest-v1.yaml` freezes 16 candidate settings: 32/64 trees, maximum depth
4/8, minimum leaf size 1/3, and unweighted versus `balanced` class weighting.
Every candidate uses a fresh scikit-learn Pipeline: median imputation and scaling
of 17 measurements, passthrough of 14 existing missing flags, and a binary Random
Forest. All-missing measurements retain their positions, use zero imputation,
and retain missing flags. Only the exact 31 `MODEL_INPUT_NAMES` enter the model;
event IDs, labels, coordinates, run IDs and future grouping attributes do not.

Seed 42, one worker, bootstrap sampling, Gini criterion and `sqrt` feature sampling
are fixed. Class balancing uses training labels only. Chronological mode fits on
training rows and selects using validation rows. Forward-CV mode refits every
candidate's preprocessing and forest inside each sufficient earlier-training,
later-validation fold; it never substitutes a random or stratified row split.
There are at most 16 candidates and three folds, plus one selected-model refit.
Configuration bounds reject excessive tree counts, depth, grid sizes and duplicate
settings. Portable models additionally have a 100,000-node budget.

Selection maximizes mean validation average precision, then minimizes mean raw-score
Brier loss. Exact ties prefer fewer trees, shallower depth, larger leaves, and
unweighted fitting. CV means give each fold equal weight. Candidate records save
paired validation identities/labels/scores, ranking and raw-score diagnostics.
The report separately compares both class-weight policies. These are development
selection results, not unbiased estimates of the selected model's generalization.
Raw forest vote fractions remain `model_score`, even when Brier/log-loss diagnostics
are computed on their bounded values.

The selected model is refitted on the original chronological training block,
or the latest sufficient CV fold's training block. It is not automatically
refitted on validation, calibration, threshold or final-test rows. Dataset,
target, feature, split, review, catalog and training-policy hashes bind the fit.
Training first recomputes partition membership and rejects inconsistent results.

## Sigmoid calibration and independent audit

Chronological mode splits the original calibration interval at its fixed midpoint.
The earlier portion fits the sigmoid, and the later portion audits it. Cutoffs
within 48 hours on either side of this internal boundary are excluded; a larger
purge in the split specification is respected. Whole source-event periods govern
assignment, including source observations without labeled snapshots. An event
spanning the internal boundary is excluded from both portions. Training,
validation, calibration-fit and calibration-audit groups remain disjoint.

Both fit and audit portions require at least four partition-event proxies and ten
rows of each class. These are versioned prototype minimum gates, not a statistical
power guarantee. At least two distinct model scores are required. The sigmoid
uses `CalibratedClassifierCV(FrozenEstimator(pipeline), method="sigmoid")` so neither
trees nor preprocessing are refitted on calibration data. Its parameters must be
finite, bounded and monotonically increasing with the raw forest score.

The independent audit saves raw and calibrated Brier loss, log loss, five-bin
reliability summaries, exact row IDs and paired scores. Calibration is accepted
only if neither loss degrades (numerical tolerance 1e-12). A rejected mapping is
recorded but disabled. Insufficient data, constant scores or an unstable mapping
also leave the forest uncalibrated. Accepted calibration enables a nullable
`escalation_probability` on the offline candidate prediction; otherwise that field
is null and output stays `model_score`. A development audit does not establish
physical severity, remove satellite observation bias, or authorize deployment.
Release evaluation/model-card review and promotion remain Phases 4.5 and 4.7.

The original chronological threshold-selection block is untouched. Phase 4.5
can select thresholds there and evaluate release performance on the still-sealed
final test. No model priority categories are invented in this phase.

Under grouped temporal CV, original development partition names are superseded by
fold identities. Original calibration/threshold rows can already participate in
fold training/validation. This phase therefore leaves CV models uncalibrated and
records `requires_cross_fitted_threshold_selection`; it does not falsely claim an
independent reserved threshold cohort. Independent calibration/threshold selection
needs a new reserved cohort or nested cross-fitting in Phase 4.5. The final test
remains sealed under every fallback, including CV without a usable final holdout.

This follows official guidance on [disjoint frozen-estimator calibration](https://scikit-learn.org/stable/modules/generated/sklearn.calibration.CalibratedClassifierCV.html)
and [Random Forest configuration](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html).

## Non-executable estimator artifacts

A fitted `model.json` saves ordered feature names, class order, training identities,
all binary tree nodes (children, feature, threshold, class fractions, impurity and
sample weights), fitted imputer medians, scaler means/scales, accepted sigmoid and
calibration identities/status. The report saves actual estimator parameters and
selection/audit evidence. Configuration, Python/NumPy/SciPy/scikit-learn and other
relevant dependency versions and every file's checksum/size are manifest-bound.

JSON prediction validates graph structure, node/depth/count bounds, feature order,
frozen feature/target specifications, finite values, scales and probability claims.
It reproduces scikit-learn's float32 forest inputs with double-precision thresholds;
training checks prediction parity against the fitted Pipeline. Calibrator output
is also checked against scikit-learn. No executable object serialization,
`pickle.load`, `joblib.load` or arbitrary estimator import occurs.

Use `load_trusted_forest(path, expected_manifest_sha256)` for trusted offline loading.
It requires a pinned digest and full verification; a package with no fitted model
cannot load as an estimator. Naked DTO construction is not registry approval.
Full verification and this trusted offline loader may recompute candidate fits;
they must remain offline CLI operations. A future deployed inference loader must
validate pinned digests and schemas without calling the training workflow.

## Commands and artifact verification

Run from the repository root using the installed environment:

```powershell
firewatch train-forest --splits data/splits/phase-4.2-final --manifest-sha256 cca85bb36cc4a207a152b38686f685de961a8b6ebb9c9ddaa807547d17d08264 --output data/models/new-forest --spec configs/forest-v1.yaml
firewatch verify-forest --path data/models/phase-4.4-final --manifest-sha256 07596df56cbc16e2567ec35ff54f6ef5a82263c3a61d1c0d31d0a728d5f0189d
python -m scripts.verify_forest
```

Outputs must be new directories. Export verifies the trusted split, computes the
bounded workflow, copies split/label provenance to private staging, saves report,
configuration and estimator JSON, verifies and publishes atomically. Failure
cleans only that export's owned staging directory. Size, dependency, path,
inventory and nested provenance checks precede any reconstruction. Offline
verification recomputes original labels, event graph, splits, candidate fits,
selection, calibration and model data. Matching recorded dependency versions are
required. Byte hashes and identities are exact; tiny numerical roundoff is allowed
in recomputed float reports. Pin the reviewed manifest digest for authenticity.
Per-observation/model packages remain Git-ignored; the public receipt contains
counts, hashes and explicit limitations.

The actual `data/models/phase-4.4-final` package reports `insufficient_data`, with
`model_available=false`, no calibration, and `model.json=null`. All twelve current
snapshots are off cadence; zero candidate fits and no real held-out metrics are
claimed. Baseline scoring remains available. The implemented training/calibration
paths are exercised with explicitly synthetic cohorts, including noisy-label
calibration acceptance and degradation rejection. Those fixtures are not real fire
training evidence. A seasonal cadence-aligned feature/label cohort is still
required before a real forest can be fitted.

See [acceptance checklist](phase-4.4-checklist.md) and [real receipt](phase-4.4-smoke.json).
