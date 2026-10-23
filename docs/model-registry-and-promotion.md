# Model registry, promotion and safe failure (Phase 4.7)

Migration `0003` adds `model_activation` (one active/previous pointer with a revision)
and append-only `model_promotions` records. Existing `model_versions` rows store
reviewed immutable identities, metrics, scope and release hashes. The database
guards managed approved artifacts against mutation/deletion, freezes per-run
approval pins and rejects scores that differ from their pinned model. The current
schema revision is shared by readiness, demo exports and contract checks.

## Offline workflow

Apply `alembic upgrade head`. Training, source verification and evaluation run
offline; they never run in an API request or inside the promotion DB transaction.
The phase-specific commands remain available. Generic entrypoints are:

```powershell
firewatch train --splits <verified-splits> --manifest-sha256 <trusted-split-sha> --output <new-training-directory>
firewatch evaluate --training <training-directory> --manifest-sha256 <trusted-training-sha> --output <new-development-evaluation>
firewatch evaluate --final --prepared <frozen-development-evaluation> --manifest-sha256 <trusted-prepared-sha> --output <new-final-evaluation>
```

`evaluate` defaults to development and leaves the final test sealed. Final access
requires both `--final` and `--prepared`; the durable Phase 4.5 one-shot ledger
continues to govern it. Keep that ledger authoritative across machines. Replaying
the same frozen evaluation for integrity does not authorize a new experiment.

Build the inference bundle from the **final** evaluation, retaining the exact
forest and development-selected thresholds:

```powershell
firewatch build-inference-bundle --evaluation <final-evaluation> --manifest-sha256 <trusted-final-sha> --scorer-version forest-v1 --output <new-inference-bundle>
```

A development-only inference candidate cannot be promoted. Promotion recomputes
source-bound final evaluation, then independently checks sufficient event/class
counts, whole-event uncertainty, positive paired AP lower bounds against both
baselines, valid bootstrap replicate counts, and forest AP improvement. These
are the frozen prototype ranking gates, not invented target accuracy or proof
of emergency-response validity. No force/bypass switch is provided.

## Independent review and promotion

The reviewer must inspect the concrete candidate, final report and model card.
Supply a separate review JSON following [the review schema](model-approval-review-v1.schema.json).
[The pending template](model-review-pending.example.json) deliberately does not
validate as an approval. Set the decision/attestations only after actual review.
The review binds exact final-evaluation, inference-manifest and model-card digests,
identifies distinct reviewer and model author, acknowledges limitations, records
a rationale, and declares supported modes. Default scope is historical replay;
live use requires an explicit generalization-review attestation. Geographic scope
is the evaluated northern-california study box; unsupported region names, boxes
or modes are refused before provider I/O.

```powershell
firewatch promote-model --version forest-v1 --evaluation <final-evaluation> --evaluation-sha256 <trusted-final-sha> --bundle <inference-bundle> --manifest-sha256 <trusted-inference-sha> --review <completed-review.json>
```

The file is a trusted operator attestation; named fields do not authenticate a
person or independently prove review occurred. Keep CLI/database write access
with trusted operators. There is no public HTTP promotion endpoint. Internal
registry transaction functions accept already validated candidates; callers must
use `prepare_promotion` first, as the CLI does.

Before the DB transaction, promotion copies the compact bundle, full evaluation
provenance, report, model card and review into a new managed release directory,
checks hashes again and pins a release manifest. The DB transaction serializes
promotions with a transaction-scoped lock, registers the approved identity,
appends its audit event and changes active/previous pointers atomically. Failures
leave no partial DB approval. A fully copied orphan after DB failure may be reused
on retry after integrity checks; it never constitutes approval by itself. Existing
release directories are verified, never overwritten. Same-version/different-
artifact approvals are refused; exact already-active promotion is idempotent.

## Enabling registry selection

`ACTIVE_SCORER=baseline-v1` and `weighted-baseline-v1` remain explicit artifact-free
choices and bypass registry selection. To use the approved active pointer, set:

```dotenv
ACTIVE_SCORER=registry
MODEL_DIR=models
```

Restart API/worker processes after changing environment configuration. Promotion
returns `registry_mode_enabled` so an operator can see whether their current
configuration selects the pointer. In registry mode, later promotions/rollbacks
apply to **newly admitted jobs** without rewriting process configuration. An empty,
retired or unavailable registry fails closed; it never substitutes a baseline.
An explicitly configured supervised version also requires a managed approved row.
For replay-only approvals, consume allowlisted replay jobs with the worker's
`--no-schedule` option. Scheduled live use requires live mode in the reviewed scope.
Readiness requires an executable registry-approved bundle; a checksum-matching
legacy opaque file alone cannot make an ML deployment ready.

Job admission records the selected version. Versions bind immutable approved
artifacts, so queued jobs keep their selected version across later promotions.
At run creation the private frozen orchestration manifest stores model ID,
version, inference digest, approval event reference and managed path. On resume
those values must still match the registry. New ML alert evidence includes its actual model ID and probability.
Completed runs return their original results, and completed outputs/alerts remain protected by the existing SQL guards.
A new model cannot replace the choice for an already-created region/mode/cutoff;
use a new cutoff for new scoring. Historical alerts are never rescored in place.

## Scoring failure and input quality

This implementation chooses explicit failure rather than automatic fallback:

- `MODEL_APPROVAL_UNAVAILABLE`: unapproved/retired identity, invalid registry
  metadata/path or run outside reviewed scope.
- `MODEL_SCORING_WITHHELD`: required data, artifact integrity, numeric inference
  or explanation validation failed. No partial batch scores are committed.

The source feature schema requires finite typed values and matching flags/units.
Observed maximum and summed FRP are required on every ML snapshot. Missing either
withholds the entire run's ML scoring, a conservative zero-tolerance required-
data rule. Nullable weather/climate and other optional measurements retain the
training-fitted medians/scales and explicit missing warnings. Missing weather is
never described as observed dry/windy conditions. There is no on-the-fly fitting,
LLM scoring or silent switch to a baseline. To use a baseline after failure, an
operator must configure one explicitly for new work.

The process pins an approved in-memory artifact once per path/digest/version.
On-disk changes do not rewrite a loaded snapshot, while fresh loads/readiness
and rollback verification reject changed files. Registry selection does not
silently evict that cache; reviewed process restarts can reset its bounded capacity.

## Rollback and retention

```powershell
firewatch rollback-model --reviewer <operator-name> --reason "Restore the preceding reviewed release"
firewatch rollback-model --version <retained-approved-version> --reviewer <operator-name> --reason "Restore a specifically reviewed release"
```

Without `--version`, rollback selects the previous pointer. It verifies the retained
release inventory and compact inference contract, changes active/previous pointers
transactionally and appends an audit record. It performs no retraining, re-evaluation
experiment or historical score modification. A retired, corrupt or missing release
cannot be activated. Keep the managed model directory and registry DB backed up
together; promotion never deletes previous approved bundles or evidence.

## Current real-data state

The original twelve-snapshot diagnostic package remains insufficient. A separate
seasonal cadence backfill now supplies eligible rows and a fitted candidate.
Nested forward development selection and the frozen one-shot final comparison
have completed. The selected forest passes both paired baseline ranking gates;
calibrated probabilities remain unsupported and null.

The [ranking release handoff](ranking-ml-release.md) records the concrete candidate,
metrics, exact artifact hashes and pending independent review. No model is approved,
registered or active; the configured scorer remains `baseline-v1`. Historical SP
results do not establish live generalization. Do not delete the committed final
ledger or rerun model selection against the opened holdout.

The [Phase 4.7 acceptance evidence](phase-4.7-checklist.md) and
[original receipt](phase-4.7-smoke.json) remain frozen historical baseline-stage
records. Their sealed-holdout preflight is not the current release verifier.
