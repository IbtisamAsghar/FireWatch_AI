# Phase 1.1 implementation and acceptance checklist

Phase 1.1 prepares accounts, tools, collaboration rules, exclusions, environment
template, documentation, and ownership skeleton. It does not implement the
runnable API/package/native PostgreSQL infrastructure assigned to Phase 1.2.

## Repository deliverables

- [x] Existing shared GitHub repository identified and clone inspected.
- [x] Environment template with placeholders and disabled optional chain.
- [x] Git/Docker exclusions for secrets, environments, downloads, models, wallets.
- [x] README with PowerShell workflow and explicit implementation status.
- [x] Architecture decisions, ownership, contribution/review policy.
- [x] Task issue template, PR template, and this acceptance checklist.
- [x] Package directories, source placeholders, config/doc handoff skeleton.
- [x] Dependency-free scaffold CI for Python 3.12 on Linux and Windows.
- [x] Local scaffold validation and staged diff checked; see validation record.
- [x] Setup changes committed on `codex/phase-1-1-setup`.
- [x] Five local module branches created from the setup commit.

## Local tools and infrastructure readiness

- [x] Git installed (observed 2.49.0.windows.1).
- [x] VS Code installed; Windows PowerShell available.
- [x] Python 3.12 installed and `py -3.12 --version` verified.
- [x] Docker CLI and Compose installed (observed 29.2.0 / v5.0.2).
- [x] Local Docker Desktop requirement removed per user preference.
- [x] Native PostgreSQL 16 setup and verification completed in Phase 1.2.

## External account and review gates

- [ ] User supplies GitHub usernames/module assignments for five team members.
- [ ] Owner grants access and collaborators accept invitations.
- [x] Setup branch published and draft PR opened.
- [x] Linux and Windows hosted scaffold checks passed on the initial setup commit.
- [ ] Independent team review and approval received.
- [x] GitHub main protection enforces PRs, one approval, and passing CI.
- [ ] Reviewed setup PR merged; module branches updated/published as needed.

These items need real account/tool evidence. They remain unchecked until
confirmed. A complete Phase 1.1 requires these external gates as well as files.

## Validation record

Initial inspection: existing main at `97ff48c`; implementation plan was an
untracked user file and is preserved. Git identity was already configured.
Python launcher initially listed 3.13 and 3.10; Docker Linux daemon initially
unavailable. Update this record after installation and repository checks.

Python 3.12.10 installed and verified. `py -3.12 scripts/check_scaffold.py`
passed with 33 required files and 32 Python files; Markdown links, environment
template and representative ignore paths validated. `git diff --check` passed.
Existing Git credential authentication has repository admin access; remote setup
can proceed without asking the user for a token. Docker startup is no longer required.

## GitHub verification

- Initial scaffold commit: `32c2493` on `codex/phase-1-1-setup`.
- Draft review: https://github.com/IbtisamAsghar/FireWatch_AI/pull/1
- Hosted checks passed on Linux and Windows:
  https://github.com/IbtisamAsghar/FireWatch_AI/actions/runs/39452707048
- Main protection was configured through the authenticated owner account:
  one approval, stale approval dismissal, strict `scaffold` and
  `scaffold-windows` status checks, conversation resolution, administrator
  enforcement, blocked force pushes and deletion.
- No direct main push or PR merge was performed. Team identities remain unknown;
  no collaborator invites were sent. The owner must supply the remaining four
  usernames if the owner is one of the five members, plus module assignments.
- Module branches use `codex/` prefixes and should follow reviewed main after merge.

## Superseded Docker readiness investigation

WSL reports default distribution `docker-desktop` and default version 2. The
Docker Desktop executable is installed. A CLI startup attempt did not expose the
Linux engine; a background application launch was then attempted. Until
`docker info --format '{{.OSType}}'` returns `linux`, Linux container readiness
was not confirmed during the original investigation. If startup shows a first-run prompt, sign-in, license,
virtualization or restart requirement, the user must resolve it interactively.
No license terms, machine reboot, or firmware settings were accepted/changed.

The user subsequently declined Docker Desktop. That supersedes the original
Docker readiness gate: no further Docker Desktop startup or troubleshooting is
required. Use native Windows PostgreSQL 16 and Python processes in Phase 1.2.
PostgreSQL installation has not yet been verified. No software was uninstalled.
