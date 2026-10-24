# Local real-data model recovery - 24 October 2026

The trained model is now present at
`data/models/recovered-seasons-v1/model.json`. It is a verified development
candidate, not an approved backend scorer. `baseline-v1` remains selected.
The machine-readable evidence is [the recovery receipt](recovered-model-training-receipt.json).

## What was trained

- Real NASA FIRMS NOAA-20 SP observations for northern California, May-October
  2022 and 2024, with the original April/November source margins.
- Both public NASA CSV archives were recovered and matched the original
  SHA-256 values in `docs/manifests/season-2022-archive.json` and
  `docs/manifests/season-2024-archive.json`.
- 17,862 source observations produced 1,975 six-hour snapshots: 157 positive,
  1,662 negative and 156 unknown labels. Unknown labels were excluded from fitting.
  A positive label means later satellite-observed FRP strengthening, not a
  confirmed fire or physical severity.
- The existing split policy produced 1,819 eligible known-label examples before
  event and purge exclusions. Conservative partitioning merged every reported
  near-neighbor link: 1,070 raw groups became 772 groups. This was an automated
  technical partition audit, not independent human incident verification.
- Grouped forward temporal CV selected 64 trees, maximum depth 8, minimum leaf
  size 3, no class weighting and seed 42. The final development fit used 1,094
  rows in `forward-3`. CV fit roles supersede the original named time blocks;
  this does not mean all 1,819 rows entered the final fit.
- None of the 440 final-test rows entered fitting. No final-test evaluation was
  performed in this recovery. The old final holdout is already known to have been
  opened; the package's `sealed` development marker does not undo that history.
- The old weather/climate cache was unavailable. Weather is missing in all 1,975
  snapshots. Missing values retain explicit flags and training-only imputation.
  Output is an uncalibrated ranking score; probability is null.

## Verification completed

The normal training exporter recomputed the source-bound split and forest before
publishing the package. Portable predictions matched the fitted estimator on
1,304 rows. A separate fresh load scored 32 real fitting examples with finite
scores and null probabilities. Model parameters and exact artifact hashes are
recorded in the receipt. No synthetic training rows were used.

## Local backend connection

An isolated PostgreSQL 16 cluster now runs on `127.0.0.1:55432`. Its data and
private bootstrap state are under `.local/model-training/`. The existing server
on port 5432 was not changed. The application database contains the recovered
real source observations and migration revision 0003.

After successful checks, `.env` was updated to connect to this database with a
dedicated application role. Its previous contents are privately retained under
`.local/model-training/previous.env`. Credentials are not included in the receipt.
The Compose environment was not changed.

Liveness, readiness, current-model metadata, regions and source status each
returned HTTP 200 using the recovered database. These are readiness checks,
not proof of a completed ML pipeline run. Training did not publish hotspot runs,
activate a region, register a model or create alerts.

From the repository root, start the API with:

```powershell
.\.venv\Scripts\python.exe -m uvicorn firewatch.main:app --host 127.0.0.1 --port 8000
```

If the isolated database is stopped, its private local recovery helper can restart
it and check migrations:

```powershell
.\.venv\Scripts\python.exe .local/model-training/prepare_training_database.py
```

Local recovery and verification helpers are retained in that same ignored folder.
Model/data directories and database credentials remain outside Git.

## What still prevents ML activation

This is a new artifact with different inputs and hashes from the old evaluated
release. The old final metrics and approval request cannot validate this model.
There is no approved runtime bundle for this candidate.

To activate ML, either recover the exact previous evaluation, inference package
and authoritative final-test ledger, or establish a reviewed new experiment with
independent validation data. The original final holdout must not be reopened for
a new experiment. Complete threshold/release evaluation and genuine independent
review before registry promotion. Historical SP validation also does not establish
live NRT generalization.

Follow [registry promotion](model-registry-and-promotion.md) after those inputs
exist. Do not select `ACTIVE_SCORER=registry` while its approved release is absent.
The backend can currently serve using the explicit baseline scorer.
