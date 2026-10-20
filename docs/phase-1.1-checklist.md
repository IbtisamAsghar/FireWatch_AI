# Phase 1.1 implementation and acceptance checklist

Phase 1.1 prepares accounts, tools, collaboration rules, exclusions, environment
template, documentation, and ownership skeleton. It does not implement the
runnable API/package/Compose infrastructure assigned to Phase 1.2.

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
- [ ] Setup changes committed on `codex/phase-1-1-setup`.
- [ ] Five local module branches created from the setup commit.

## Local tools and infrastructure readiness

- [x] Git installed (observed 2.49.0.windows.1).
- [x] VS Code installed; Windows PowerShell available.
- [x] Python 3.12 installed and `py -3.12 --version` verified.
- [x] Docker CLI and Compose installed (observed 29.2.0 / v5.0.2).
- [ ] Docker Desktop running with a working Linux engine.

## External account and review gates

- [ ] User supplies GitHub usernames/module assignments for five team members.
- [ ] Owner grants access and collaborators accept invitations.
- [ ] Setup branch published, PR opened, hosted CI green, independent review.
- [ ] GitHub main protection enforces PRs, one approval, and passing CI.
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
can proceed without asking the user for a token. Docker startup is still pending.
