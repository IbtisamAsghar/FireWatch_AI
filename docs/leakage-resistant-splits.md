# Leakage-resistant partitions (Phase 4.2)

Phase 4.2 provides reproducible event grouping, chronological partitions, purged
forward development folds, training-only preprocessing, and offline package
verification. The current real cohort selects the documented `baseline_only`
fallback. There is no trained estimator or evaluated final holdout in this phase.

## Whole-period grouping and review

`configs/event-groups-v1.yaml` freezes an undirected thermal-activity graph: connect
observations at most 4 km and 48 hours apart, inclusively; use connected components
across the complete two-season source inventory, including observations without
feature snapshots. NOAA-20 standard-processing data and the Northern California
bbox are required. Every day from April 30 through November 1 in 2022 and 2024
must have complete source-delivery inventory. Missing days fail closed. Complete
source delivery does not establish cloud-free satellite observation coverage.

Physical identities determine stable event IDs. Revision aliases connect existing
feature members to the seasonal graph. No FRP outcome, target label, model score,
weather, or future-derived event attribute enters feature construction. The model
whitelist remains exactly 17 numeric values and 14 missing flags. Grouping uses
future observations solely to keep entire periods together for partitioning.

The inspection report flags groups exceeding 50 km centroid radius, 30 days, or
5,000 observations. Distinct components with observation pairs within 8 km/72 hours
require a documented conservative merge or a reasoned separation. A review binds
the exact catalog hash and every component decision. Unreviewed components and
unresolved neighboring pairs cannot qualify for supervised release. Connected
components cannot be split by the review format; exclude a suspect component or
propose a new versioned grouping policy and regenerate all partitions. Truncated
near-miss inspection also prevents supervised readiness.

The real inventory contains 17,862 observations, 1,070 raw components, and 340
near-miss pairs. Local technical review merges all ambiguous neighbors through
148 disjoint merge sets, producing 772 partition groups. The large components
were inspected as indivisible periods; conservative overmerging is intentional.
These counts describe partition proxies, not independently verified wildfire
incidents. The review identifies itself as Codex local review and makes no claim
of independent human incident verification. Retain this limitation in research.

## Frozen time blocks and leakage controls

`configs/splits-v1.yaml` declares train May–October 2022, validation May 1–June 15
2024, calibration June 15–July 15, threshold selection July 15–August 15, and final
test August 15–November 1. Intervals are UTC and half-open. Remove cutoffs within
48 hours on each side of every boundary, including endpoints. This conservative
96-hour gap separates 24-hour lookbacks and 24-hour future-label windows.
Any whole event whose source-observation period touches multiple blocks is
excluded in full; it cannot leak through snapshots missing on one side.

Eligibility retains all Phase 4.1 exclusions, unknown labels, cadence constraints,
and exact feature/target binding. Save every excluded row and reason, not only
accepted rows. Baseline reference years 2010–2019 and the existing feature
specification are SHA-bound before final-test use. The original Phase 4.1 package
is immutable; its deferred split-policy field records its historical stage.
The new split specification supplies the separately frozen partition policy.

Prototype sufficiency requires at least three training events, two events in each
evaluation block, and five rows of each class in each block. These are minimum
software gates, not evidence of statistical power or calibration accuracy.
If the five blocks are insufficient, three expanding, purged, event-disjoint
forward folds are attempted within development data. Two sufficient folds are
required. A final holdout remains separate wherever usable. CV without a usable
final holdout supports development research only and retains a baseline release
recommendation. No sufficient folds means `baseline_only`.

`development_rows` is the sole development selector for future model searches;
it refuses final-test access. `fit_preprocessor` fits median imputation and scaling
on training rows only, with an explicit training fold required in CV mode.
All-missing numeric columns keep their positions and their existing missing flags.
Validation/calibration/threshold rows use only the fitted transform. Hashes bind
fit rows and dataset; changing data or folds requires refitting. Future estimator
and hyperparameter code must use these selectors and remain inside development.
This phase does not perform the model searches planned in Phases 4.3–4.4.

The approach follows scikit-learn guidance on [training-only preprocessing](https://scikit-learn.org/stable/common_pitfalls.html)
and [time/group-aware validation](https://scikit-learn.org/stable/modules/cross_validation.html).

## Operator workflow

Run from the repository root with its installed environment. Trusted hashes below
come from the previously verified local handoffs; do not replace them merely to
make a verification failure disappear.

```powershell
firewatch group-split-events --sources data/member-4/phase-2.6-final-handoff --source-sha256 7b2c87453b24e5669297128232d30e8f2ca6a76a5fe3efd65452c5d2f4eafe0f --output data/splits/new-event-catalog.json
# Inspect the report and record decisions using the event-review schema.
firewatch build-splits --labels data/labels/phase-4.1-final --label-sha256 15e7bf0aa34e2521e89b84fc0a3bf460753ccf47b84229594ad48719611cbdae --sources data/member-4/phase-2.6-final-handoff --source-sha256 7b2c87453b24e5669297128232d30e8f2ca6a76a5fe3efd65452c5d2f4eafe0f --review data/splits/phase-4.2-event-review.json --output data/splits/new-reviewed-splits
firewatch verify-splits --path data/splits/phase-4.2-final
python -m scripts.verify_splits
```

Outputs must be new paths. Export uses owned atomic staging and verifies before
publishing. The portable package contains frozen label inputs and follow-up,
event observations and review, split specification, memberships, fold identities,
counts, exclusions, and checksummed manifest. Verification requires no database,
provider, or credentials: it checks paths, sizes, hashes, nested label recomputation,
rebuilds the complete graph, reapplies review, and recomputes every partition.
For artifact transfer, pin the manifest hash from the acceptance receipt with
`verify-splits --manifest-sha256 <trusted-hash>`; self-consistency alone does not
establish authenticity. Private per-observation packages remain Git-ignored.

## Current real result and next phase

The twelve diagnostic snapshots contain eight unobserved negatives and four
unknown wide-cluster labels. All twelve are off cadence; every partition and CV
fold has zero eligible rows. `training_ready=false`, `baseline_only`, and
`final_test_status=sealed` are the correct result. The engine supports larger
eligible cohorts, as exercised by explicitly synthetic balanced test fixtures.
Synthetic fixtures are never used as real training evidence.

Proceed to Phase 4.3 baseline implementation. Supervised comparison needs seasonal
cadence-aligned past-only features, complete separately pinned follow-up labels,
reviewed partition proxies and sufficient independent events/classes. Then
regenerate a new source-bound package under the frozen policy before training.
