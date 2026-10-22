# Batch inference and explanations (Phase 4.6)

`firewatch.ml.predict.score_features(features, scorer_version, scored_at=...,
approved=...)` is the shared batch contract. It validates the complete batch before
loading a model and returns one `ScoreResult` per input in the original order,
including repeated inputs. Maximum batch size is 256. The worker chunks larger
baseline runs and uses one scoring timestamp. Invalid input or inference failure
raises an explicit error; no results are returned and no alternative method is
silently substituted.

Each result includes scorer version, model ID for supervised scoring, score kind,
priority score, nullable escalation probability, priority, one to three deterministic
reasons, a structured explanation, quality flags and explicit warnings. Warnings are
also saved in explanation JSON so the existing database and hotspot API preserve
them without a migration. Baseline numeric ranking/cutoffs remain unchanged;
explanations now expose their actual score terms and provisional category warnings.

## Trusted artifact boundary

Baseline scorers need no model or approval reference. Supervised scoring requires
an `ApprovedScorer` supplied by the application's trusted approval authority, with
model ID, scorer version, artifact directory, manifest SHA-256, explicit approved
status and an approval reference. Never deserialize this reference from an
untrusted HTTP request or infer it from a candidate bundle. This type represents
an approval already made; it does not perform model review or promotion.

Compact bundles contain only four bounded JSON files: a manifest, portable forest,
frozen forest thresholds and exact feature contract. The manifest pins all files,
scorer identity, evaluation-source SHA-256 and numeric runtime versions. Candidate
bundles identify themselves as candidates, never as approved models. Loading checks
trusted manifest digest, inventory, file digests, units, thresholds, calibration,
feature definitions/order and valid training path covers. It executes no serialized
code, loads no pickle/joblib, reads no training dataset and performs no fitting.

The runtime caches up to eight identities keyed by canonical artifact path, digest
and version. A lock ensures concurrent first use loads/compiles only once. The
loaded snapshot remains pinned if on-disk files subsequently change. New approved
digests/versions create separate cache entries; a full cache fails explicitly rather
than evicting an existing identity. Restart the worker for a reviewed cache reset.
Processes do not share this memory cache. Deployment and rollback are Phase 4.7.

Build a compact **candidate**, preserving the frozen development forest and its
score-scale thresholds, with:

```powershell
firewatch build-inference-bundle --evaluation <verified-evaluation-directory> --manifest-sha256 <trusted-evaluation-sha256> --scorer-version forest-candidate-v1 --output <new-bundle-directory>
firewatch verify-inference-bundle --path <bundle-directory> --manifest-sha256 <trusted-bundle-sha256>
```

Development or final evaluation artifacts can supply a candidate. A development
bundle does not establish held-out release performance. Neither CLI command grants
approval, opens a new final-test experiment, changes the active scorer or updates
the registry. Phase 4.7 must resolve approved records, check release evidence and
connect that trusted reference to worker requests. The current worker intentionally
accepts only its configured baselines until that registry workflow exists.

## Input and missing-data policy

Inference requires the frozen features-v1 payload/hash, units, missing flags and
feature-configuration SHA-256. Numeric values must be finite and satisfy their
physical/type bounds. Unknown features, changed configuration/version, corrupt
payload hashes and mismatched missing flags fail validation. Mapping insertion
order is immaterial: input arrays are always assembled in the exact 31-name model
order declared by the bundle (17 measurements, then 14 missing flags). Manifest
order changes are rejected. Prediction and explanations share the same float32
transformation and <= branch semantics as the trained estimator.

Observed maximum and summed FRP are required for ML. If either is missing, ML
scoring is withheld. Other nullable measurements use the model's training-fitted
medians/scales and retain their missing flags; warnings identify each missing
measurement and source quality issue. No current-batch fitting or filling from
future observations occurs. This prototype quality rule does not establish
operational validity under arbitrary distribution shifts.

## Actual SHAP contributions

The explainer computes exact Shapley values for the tree-path-dependent game,
using weighted training node covers to integrate absent features. This follows
the [Tree SHAP framework](https://www.nature.com/articles/s42256-019-0138-9)
and explicitly selects the semantics described by the
[TreeExplainer documentation](https://shap.readthedocs.io/en/latest/generated/shap.TreeExplainer.html).
It is not a global feature-importance ranking or a split-gain proxy. Covers include
training/class/bootstrap weights, so the background is that weighted distribution.

For a leaf, combine repeated tests of each feature into its known path indicator
and the product of its unknown child/parent cover fractions. Its coalition value
is the leaf value times the product of known/unknown factors. The Shapley marginal
for feature j is the integral from 0 to 1 of (known_j - unknown_j) times the product
of all other factors interpolated at t. This is a polynomial of degree at most
path-depth minus one. Gauss-Legendre quadrature with ceil(unique-path-features/2)
points integrates it exactly up to floating-point error. Contributions sum across
leaves and trees, yielding polynomial O(leaves * depth squared) work per row.
Repeated split features are combined before integration; unused features have
zero contributions. No permutation sampling or SHAP dependency is required.

The explanation saves all 31 signed contributions, expected base value, raw model
score, input order, missing/imputed states and imputed values. Units are
`raw_positive_class_forest_vote_fraction`. It checks that base plus contributions
matches the actual raw forest score; mismatch or invalid covers withhold inference.
The implementation is independently checked against exhaustive coalition Shapley
values on small weighted trees, including repeated splits, and exercises depth 12.

An audited sigmoid may transform the raw score into calibrated probability and
use its matching frozen thresholds. SHAP still explains the **raw underlying
forest output**. Its contributions do not sum to the sigmoid probability, do not
establish causality, and are not effects in MW or physical fire severity units.
An uncalibrated forest uses `score_kind=model_score` with null probability.

Reasons select up to three nonzero contributions by descending absolute magnitude,
with feature-name ties. Positive contributions raise and negative contributions
lower the raw score relative to its reference. A missing measurement is explicitly
called missing with training-median imputation; missing-flag contributions identify
the indicator state. Missing weather is never described as observed dry or windy
conditions. A constant explanation gives one honest no-contribution reason.

## Baselines and current real state

The FRP baseline exposes its actual maximum-FRP term in MW. The weighted baseline
exposes all four weighted terms in index points (including missing zero floors)
and derives its three reasons from those terms. Neither baseline invents SHAP,
a model ID, calibrated probability or validated severity. Numeric weights and
provisional cutoffs are unchanged.

The current twelve snapshots are all off cadence for supervised evaluation, and
there is no fitted eligible forest, selected ML thresholds or approved model.
Candidate export correctly refuses this state. Real baseline scoring and warnings
remain usable; synthetic tests demonstrate the ML contract without claiming real
fire performance. See [acceptance](phase-4.6-checklist.md),
[real receipt](phase-4.6-smoke.json) and [model-card limits](model-card.md).
