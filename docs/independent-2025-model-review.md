# Model ready for independent review

Status on 24 October 2026: **approved by Talha and active for historical replay**.
Talha stated that he reviewed the candidate and explicitly confirmed approval
for historical northern-California replay with the documented limitations
acknowledged. The completed review is retained inside the managed release;
the pending JSON remains an unapproved historical template. Registry activation
and a real weather/climate-enriched ML run were verified through the API/worker.
See [the activation receipt](local-real-readiness-receipt.json).

## Review outcome requested

A person other than the model author must review the exact report, model card,
limitations and artifact pins below, and decide whether to approve historical
northern-California replay. Reviewer identity and a real rationale are required.
The model-development author for this recovery is Codex (automated development
agent). Talha supplied the independent review confirmation. The pending JSON is
deliberately invalid as an approval. Never fill its attestations just to pass a
schema. Live use is outside this requested scope.

## What the evidence supports

- Real NASA FIRMS NOAA-20 SP observations for northern California; no synthetic
  training or final-test examples. Total local source observations: 19,771.
- Original May-October 2022 and pre-15-August-2024 development data, with temporal
  and conservative event grouping. The previously consumed 2024 final period
  was excluded, and its missing-ledger recovery block prevents another experiment
  reopening that period.
- New May-October 2025 final holdout: 811 eligible rows, 47 positive and 764
  negative, across 438 thermal partition groups. The existing one-shot ledger
  now records this final evaluation complete. Do not retune against these results.
- Frozen features/target, nested forward model and threshold selection, 48-hour
  purges, source-bound deterministic verification and exact runtime bundle pins.
  A pre-final support-boundary correction is documented in
  [the audit amendment](2025-validation-pre-test-amendment-v2.md). Earlier
  development v1 artifacts were abandoned before final access.
- Forest final average precision: **0.109701**; maximum-FRP baseline: **0.050746**;
  weighted baseline: **0.055594**. Paired whole-event 95% intervals for forest AP
  improvement were **[0.020698, 0.130192]** and **[0.008255, 0.124696]**, with all
  200 bootstrap replicates valid for each. These pass the existing ranking gates.
- Logistic comparison AP was **0.139349**. Forest improvement over logistic was
  not established. Weather/climate ablations did not establish additional skill.
  This is evidence against the two project baselines, not a claim that the forest
  is the best possible method.

## Limitations the reviewer must acknowledge

The target is later satellite-observed FRP strengthening, not confirmed fires,
physical severity, burned area or emergency advice. Partition groups are thermal
proxies and snapshots are not independent verified incidents. Clouds, overpass
timing and unobserved negatives limit interpretation.

Training and final weather/climate coverage is sparse and missing values remain
flagged with training-only imputation. The separately published frontend replay
now has real weather and climate on all six hotspots; that does not retroactively
fill the frozen training data or prove weather-sensitive model skill.

The forest is uncalibrated: `model_score` is a ranking score and probability is
null. Priorities are provisional research categories. Historical NOAA-20 SP
results do not establish live NRT or Pakistan-region generalization. Requested
approval scope is historical replay inside the evaluated northern-California box.

## Exact review artifacts

- Final report/card: `data/evaluations/independent-2025-final-v2/`.
- Inference bundle: `data/inference/independent-2025-v2/`.
- Scorer version: `forest-independent-2025-v2`.
- Evaluation manifest SHA-256:
  `fdbd705d6c1fe088872a548c39626fb15e89e71b17aaccea61ad871e8de4d02f`.
- Inference manifest SHA-256:
  `40506f4ac97c0b75522afd7b16a7d0d9c19f9ebb33affe8f2c9484c493e15b51`.
- Model card hash and pending exact review: `independent-2025-review.pending.json`.
- Preserve `data/evaluations/final-test-ledger.sqlite3`, both protocol records,
  source data and all nested evaluation artifacts with any transfer/backup.

## Activation after actual review

Save a separate completed review following the existing
`model-approval-review-v1` schema, with the real independent reviewer's name and
rationale, exact hashes, acknowledged limitations and only `replay` support.
Do not modify the pending file into a fake completed review.

The prepared local helper invokes the normal promotion checks, verifies registry
readiness and only then sets the local scorer to registry:

```powershell
.\.venv\Scripts\python.exe .local/model-training/activate_reviewed_model.py <completed-review.json>
```

Restart the API and use a replay-only worker (`--no-schedule`). A checksummed
ML-specific replay input bundle is already prepared at
`data/replays-ready-v1/norcal-ml-weather-v1/`; its manifest fixes the candidate
version and real weather/climate inputs. Existing baseline results remain unchanged.

See [local backend readiness](local-real-readiness.md) for the active ML run and
preserved baseline results.
