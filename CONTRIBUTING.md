# Contributing

## Branches and pull requests

Shared `main` receives reviewed PR merges only. Create task branches with
the `codex/` prefix. Module branch names are `codex/module-1-api`,
`codex/module-2-firms`, `codex/module-3-pipeline`, `codex/module-4-ml`, and
`codex/module-5-delivery`. Branch owners are assigned after team access is set.
Start new work from the latest reviewed `main`; merge the initial setup PR
before building on its scaffold. Keep PRs focused on one phase or small
vertical slice. Do not merge your own PR without another member's approval.

Require one approval, resolve conversations, dismiss stale approvals after
changes, and pass `Repository checks / scaffold`. The owner configures server
enforcement using [the GitHub checklist](docs/github-setup.md). Written rules
alone do not enforce branch protection. Do not bypass protections or force
push shared branches. Use GitHub's reviewed PR merge process.

## Shared contracts

Member 1 owns migration numbering and reviews changes to shared SQLAlchemy
models, DTOs, and REST contracts. Coordinate before modifying another member's
area. Version feature/evidence/API contracts when compatibility changes.
Use UTC, documented units, typed interfaces, explicit missing values, and
isolated run IDs. Keep replay isolated from live output.

## Before opening a PR

- Link the phase/issue and describe the user-visible outcome and scope.
- Run `py -3.12 scripts/check_scaffold.py` from the repository root.
- Run the meaningful tests required by implemented behavior when available.
- Inspect `git diff --check` and `git diff --cached` before committing.
- Confirm there are no real credentials, data downloads, wallets, private
  keys, model bundles, or generated archives in the commit.
- Record validation, known gaps, and changes to contracts/documentation.

Only commit reviewed, small fixtures with provenance and license notes to
`data/sample`. Synthetic fixtures must be labeled synthetic. Runtime raw data
and model artifacts stay outside Git. Approved bundles require checksums and
manifests in later phases. Never load arbitrary pickle/joblib artifacts.

## Local development conventions

Use Python 3.12 and PowerShell locally; shared services use Linux containers.
Follow `.editorconfig` and `.gitattributes`. Do not alter global Git/editor
settings for this project. Follow the plan's exact dependency locking in
Phase 1.2. Do not add Redis, paid APIs, a frontend, or chain dependencies to
the initial backend foundation without a documented scope decision.
