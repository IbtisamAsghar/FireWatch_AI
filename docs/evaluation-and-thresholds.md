# Evaluation and priority thresholds (Phase 4.5)

The offline evaluation workflow freezes six paired scorers, development thresholds,
source hashes, dependency versions and a model card before final-test access.
The live scorer remains the explicitly configured baseline. Phase 4.6 inference
and Phase 4.7 registry approval are separate requirements.

## Frozen protocol

[Evaluation policy](../configs/evaluation-v1.yaml) sets bounded 200-replicate,
seed-42 whole-event bootstrap intervals (95%), at least five partition events
for uncertainty, at least two threshold events/five rows per class, and at least
five final events/ten rows per class. These are prototype sufficiency gates,
not statistical power or operational-validity guarantees.

Chronological evaluation fits weighted references, preprocessing, logistic and
weather/climate ablations on training rows only. It preserves the forest's
validation selection and audited calibration; the original threshold block
alone chooses priorities. The forward-CV fallback reserves its latest sufficient
outer validation fold for thresholds and reselects forest hyperparameters using
at least two earlier sufficient inner folds wholly inside outer training.
The original CV candidate is replaced because its selection saw outer validation.
Fewer than three sufficient folds cannot support this procedure. CV outputs
remain uncalibrated model scores; calibration is never manufactured.

Six methods use identical rows: maximum-FRP baseline, weighted baseline,
logistic margin, forest, forest without weather/climate, and forest without
climate. Ablations refit the selected bounded forest configuration with the
excluded measurements **and their missing flags** removed. The full model's
preprocessing is never reused for these fits. Ablation findings concern this
fixed configuration and cohort; they do not establish causal feature effects.

## Metrics and uncertainty

Each cohort reports precision, recall, F1, prevalence, counts and confusion matrix
with actual classes as rows and predicted classes as columns, ordered [0,1].
It saves both trapezoidal precision-recall AUC and average precision, naming them
separately. With no positives, recall/AP/PR-AUC are undefined, not zero.
Precision with no predicted positives and F1 with a zero denominator use zero.

Within each pipeline run, descending score and stable row-ID ties determine the
top k=min(10,n). Macro precision includes all runs. Macro recall excludes
zero-positive runs and reports excluded/included counts. Small strata preserve
these same definitions. Breakdowns cover partition event, month, sensor/product,
weather completeness and unobserved follow-up. These may be highly sparse and
are not independent inferential tests.

Only an accepted calibrated forest supplies probability, Brier score and a
five-bin standalone SVG reliability figure. Logistic decision margins and raw
forest votes remain model_score; probability stays null. No probability plot
is rendered without supported probabilities.

Bootstrap draws whole partition events with replacement, retaining all snapshots,
and uses identical draws for every method. Percentile intervals and paired
forest-minus-comparator AP differences report valid replicate counts. Fewer than
five events produce no interval. These are thermal-activity partition proxies,
not independently verified fires; uncertainty remains conditional on those groups.

## Priority policy

Each score scale selects ordered Medium/High/Critical boundaries from the
reserved development cohort, maximizing F2/F1/F0.5 respectively (highest cutoff
breaks objective ties); scores below Medium are Low. Boundaries are stored with
method, units, exact development row identities and provisional status.
Tied scores can collapse bands, explicitly reported in metadata/model card.
Development metrics are optimistic selection diagnostics, never release results.

The no-data artifact retains only existing explicitly provisional display rules:
FRP 10/50/100 MW and weighted index 35/55/75 on its 0–100 scale. These are not
probabilities or validated physical severity boundaries. There is no default
0.35/0.55/0.75 forest probability claim.

## Commands and final-test governance

Run from the project root using its installed `firewatch` command:

```powershell
firewatch prepare-evaluation --training data/models/phase-4.4-final --manifest-sha256 07596df56cbc16e2567ec35ff54f6ef5a82263c3a61d1c0d31d0a728d5f0189d --output data/evaluations/phase-4.5-final
firewatch verify-evaluation --path data/evaluations/phase-4.5-final
```

After adequate eligible development and final cohorts actually exist, freeze a
new development artifact, review its model card and pin its manifest SHA-256.
Then `firewatch evaluate-final --prepared <frozen-directory> --manifest-sha256
<trusted-sha256> --output <new-final-directory>` consumes the final holdout once.
The current sparse artifact refuses this command before creating a ledger.

The authoritative ledger is `data/evaluations/final-test-ledger.sqlite3` relative
to the project working directory. Keep and back up this private ledger with the
project; all operators/machines must use the same authoritative copy. Switching
working directories, deleting the ledger or cloning without it cannot preserve
cross-run governance. This local tool does not provide a central multi-machine
approval service or resist an administrator intentionally deleting its records.
There is no CLI reset, override or force flag.

An SQLite transaction claims the region/target/final-period key before final
scoring, independent of model/config/source revisions. Failure consumes the claim.
A second output/experiment is rejected. If publication succeeded but recording
completion failed, only the exact source-bound saved result at its original path
can be verified and completed; it does not rerun a selectable experiment.
`verify-evaluation` mechanically replays the same frozen protocol for integrity,
including the fixed final scores. It is not permission to change thresholds,
models or policy after seeing final results. Private packages include all nested
provenance and use bounded checksummed JSON, never pickle/joblib loading.

The recommendation requires the lower paired event-bootstrap AP difference to
be above zero against **both** baselines with at least 90% valid replicates.
Even a positive recommendation requires independent model-card/limitations review
and Phase 4.7 registry approval; this workflow never promotes a model itself.
Absent evidence, retain baseline-only. Weather/climate improvement is reported
only when its paired ablation interval supports it.

## Current data and handoff

All twelve current snapshots violate the frozen six-hour cadence. There are zero
eligible evaluation rows, so the real report contains no fitted evaluation models,
selected ML thresholds, held-out metrics, reliability plots or ablation claims.
The final test remains sealed and no ML promotion occurred. Do not relax the
cadence, invent labels, relabel unknown follow-up as negative, or use seasonal
point observations as event-feature snapshots to create apparent readiness.

To enable empirical evaluation, supply historical feature snapshots on the frozen
00/06/12/18 UTC cadence with source-bound follow-up, reviewed disjoint event groups,
both classes and coverage across training/validation/calibration/threshold/final
periods. The existing protocol validates those packages when available.
See [acceptance evidence](phase-4.5-checklist.md) and [real receipt](phase-4.5-smoke.json).
