# GitHub owner setup

Repository: https://github.com/IbtisamAsghar/FireWatch_AI

The shared repository exists. Local scaffolding does not invite collaborators,
push branches, create hosted issues, or apply server-side protection.

## Team access

Collect the GitHub usernames for five members, including the repository owner
when applicable. In Settings > Collaborators, invite the remaining members
with write access (not admin). Confirm invitations are accepted. Assign the
five module owners from the implementation plan. Do not invent usernames or
place unaccepted invitees in CODEOWNERS as if access were confirmed.

## Publish the prepared setup

Push `codex/phase-1-1-setup` and open a PR to `main`. After the workflow runs,
require an independent review. Merge through the PR process. Refresh module
branches from the merged main before beginning module implementation.

## Protect main

In Settings > Rules > Rulesets (or Branches > protection), target `main`:

- Require a pull request and at least one approving review.
- Dismiss stale approvals and require conversation resolution.
- Require status check `scaffold` from workflow `Repository checks`; select
  the actual check after its first hosted run. Require branches to be current.
- Block force pushes and deletion. Apply rules to administrators or use an
  empty bypass list where supported. Do not allow routine direct main writes.

Record the saved rule and a successful hosted CI run in the Phase 1.1
checklist. Verify enforcement on a disposable branch/PR without changing main.
GitHub plan/repository visibility can affect ruleset availability; if unavailable,
record that limitation explicitly. Do not claim documentation enforces a rule.

## Issues and review

The task template under `.github/ISSUE_TEMPLATE` and PR template become
available on GitHub after merge. Use the local phase checklist to create
tracked issues for pending setup and subsequent phases. Do not create duplicate
issues for work already tracked. GitHub write/admin authentication is required
to perform account actions; no account tokens belong in repository files.
