# Ranking-first ML release handoff

The candidate passed the frozen final ranking gates. It is ready for independent
review, but is **not approved or active**. `baseline-v1` remains selected.
Probability stays null. This candidate supports consideration for historical
northern-California replay; these results do not establish live generalization.

## Verified evaluation

Development selected a 64-tree, depth-8 forest with minimum leaf size 3 and
balanced class weights using nested forward selection. This replaces the original
32-tree development training candidate. The final comparison used fixed models
and thresholds on 440 rows, including 33 positives and 407 negatives across 170
thermal partition groups. These groups are proxies, not independently verified fires.

Final average precision was 0.190205 for the forest, 0.0529941 for `baseline-v1`
and 0.0622052 for `weighted-baseline-v1`. Average precision measures ranking;
it is not accuracy or an individual hotspot probability. The paired whole-event
95% intervals for forest AP improvement were [0.0494502, 0.261606] and
[0.0470325, 0.247841], respectively, with all 200 bootstrap replicates valid.
At the selected High threshold, precision was 0.12 and recall 0.636364;
false positives remain substantial. No further tuning used the final results.

Frozen forest Medium/High/Critical boundaries are 0.1619925990872335,
0.23068112118592887 and 0.5419226103324075 in raw forest score units.
They are provisional research priorities, not physical fire-severity categories.
Weather is missing in 1,969 of 1,975 source snapshots. This release does not fix
weather coverage or calibrated percentages. Missing optional values retain
training-fitted imputation and explicit flags. Additional weather/calibration
work requires a new reviewed experiment and independent evaluation data.

The final ledger is committed. Preserve `data/evaluations/final-test-ledger.sqlite3`
with the artifacts across machines. Never delete it to rerun selection against
this holdout. The earlier Phase 4 and backfill receipts describe their historical
stages; their sealed-holdout checks are not current release verification commands.

## Concrete review inputs

The [public receipt](ranking-release-receipt.json) records hashes, metrics and
verification performed. An isolated installed-wheel check reproduced all 440
final-row scores and priorities, confirmed null probabilities and SHAP additivity,
and denied settings, provider, database, pickle and fitting access. Private local
artifacts remain Git-ignored:

- Final report and model card: `data/evaluations/cadence-final-v1/`.
- Compact candidate: `data/inference/cadence-ranking-v1/`.
- Exact pending review: [cadence-ranking-review.pending.json](cadence-ranking-review.pending.json).

Trusted final manifest SHA-256: `ddb9e58821bd8d676c32c9213820352ada3c59093b0169a3db57b0139a28f931`.
Trusted inference manifest SHA-256: `b564e317491e2caf19279948ed2aa6cfb80f3496741e87aa5c4505472c904986`.
Trusted model-card SHA-256: `6224bfbf9bf1fbcd9da10e429cba9ea33199c39534c75f249f77e861b686df2d`.

An independent reviewer must inspect the final report/model card and limitations,
provide their real identity and rationale, acknowledge the limitations, and
explicitly approve historical replay. The pending review deliberately cannot
validate as approval. Do not fill attestations merely to satisfy the schema.
The [project promotion contract](../src/firewatch/ml/registry_artifacts.py)
requires distinct named author/reviewer and binds these exact artifact hashes.

Only after genuine review, save a separate completed review and run:

```powershell
firewatch promote-model --version forest-cadence-v1 --evaluation data/evaluations/cadence-final-v1 --evaluation-sha256 ddb9e58821bd8d676c32c9213820352ada3c59093b0169a3db57b0139a28f931 --bundle data/inference/cadence-ranking-v1 --manifest-sha256 b564e317491e2caf19279948ed2aa6cfb80f3496741e87aa5c4505472c904986 --review <completed-review.json>
```

Then configure `ACTIVE_SCORER=registry`, restart API/worker processes, and verify
the active identity/readiness. A replay-only release uses the worker's
`--no-schedule` mode. Live approval requires additional actual generalization
review; it is not implied by this evaluation. Follow the
[registry runbook](model-registry-and-promotion.md) for admission pins and rollback.
