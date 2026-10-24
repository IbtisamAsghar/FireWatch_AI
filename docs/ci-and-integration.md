# Phase 5.5: CI and integration quality

The repository workflow runs on pull requests, pushes to `main`, `development`
and `codex/**`, release tags `v*`, and manual dispatch. It does not publish an
image or deploy infrastructure. CI needs no FIRMS, weather, admin or chain secrets.
Synthetic operator tokens and the ephemeral PostgreSQL service password are test
fixtures only. Standard pytest jobs set `FIREWATCH_OFFLINE_TESTS=1`: external
Python socket connections and DNS lookups fail, while loopback PostgreSQL remains
available. Installing tools/dependencies and querying advisory databases requires
internet access; test execution does not require external provider availability.

## Required checks

| Check | What it verifies |
| --- | --- |
| `scaffold`, `scaffold-windows` | Python syntax, local links, secret exclusions, hashed locks |
| `backend-linux` | Ruff, formatting, strict mypy, complete pytest/coverage against PostgreSQL 16, migration integrity, OpenAPI/handoff, local Anvil delivery, wheel/sdist and runtime-only installation |
| `backend-windows` | Unit/contract collection and native Python package behavior |
| `solidity-contracts` | Checksum-pinned Forge/solc, registry tests, isolated loopback deployment |
| `dependency-and-secret-scanning` | Locked dependency advisories, security-tool advisories, redacted Git history and worktree secret scans |
| `linux-container-build` | Linux package image, non-root identity, CLI overrides, API boot/liveness/OpenAPI, failed DB readiness, image identity |

Configure these actual hosted check names as required checks after their first
successful run; repository files alone cannot enforce server-side branch rules.
The frontend still consumes the same 18 API operations. The independently saved
`tests/fixtures/openapi-v1.json` freezes the reviewed wire contract. Any intentional
change needs a frontend compatibility review before regenerating that fixture.

## End-to-end and failure coverage

`tests/integration/test_pipeline_end_to_end.py` reads committed synthetic HTTP
transcripts through the real FIRMS client/normalizer/importer, clusters records,
selects cached enrichment, builds features, executes the durable worker, and reads
typed list/detail/summary/GeoJSON/job/verification responses over ASGI HTTP. It
checks unchanged canonical alert bytes, artifact hashes and replay/live isolation.
Three variants cover enriched baseline, missing-enrichment baseline, and a locally
fitted synthetic approved model. The latter uses deterministic test training and
JSON serialization, never an external pickle or a production model approval.

| Failure/integrity behavior | Existing acceptance suite retained in full CI |
| --- | --- |
| Crash after each stage and after publication | `tests/integration/test_worker.py` |
| Duplicate job submission and concurrent admission | `tests/integration/test_operator_api.py` |
| Partial provider outage and successful empty region | `tests/integration/test_worker.py` |
| Missing/unapproved/corrupt model, withheld scores | `tests/integration/test_model_registry.py` |
| Replay/live isolation and frozen inputs | `tests/integration/test_replay.py`, `test_worker.py` |
| Exact canonical bytes/hash and tamper handling | `tests/unit/test_evidence.py`, `tests/integration/test_evidence_verification.py` |
| All read response DTOs and frontend handoff | `tests/contract/test_read_acceptance.py`, `test_handoff_bundle.py` |
| Signed-transaction recovery, outages and reorgs | `tests/integration/test_anchor_delivery.py`, `scripts/verify_anchoring.py` |

Provider fixtures are explicitly synthetic; these software checks establish no
scientific model performance or production data availability.

## Security gates

`requirements-security.lock` isolates pip-audit 2.10.1 and its fully hashed
dependencies from application/runtime locks. Both the development lock (which
contains the complete runtime lock) and security-tool lock are audited. Advisory
or service errors fail the check; there are no ignored vulnerability IDs.

The initial scan reported CVE-2026-25087 for PyArrow 21.0.0. Phase 5.5 pins
PyArrow 23.0.1, the fixed release; all other application/development package
versions are preserved. Historical receipts and previously produced artifact
hashes remain unchanged. New Parquet exports record the new writer version.
See the [Apache advisory](https://arrow.apache.org/blog/2026/02/16/23.0.1-release/).

`python scripts/scan_secrets.py` downloads Gitleaks 8.30.1 only from its official
release and checks the pinned platform-specific SHA-256. Cached archives are
reverified and the executable is reextracted on every run. A generated negative
control must be detected before scanning. Full available Git history is scanned,
and a separate snapshot covers tracked plus nonignored untracked files, so local
`.env`, data archives and wallets never enter the worktree report. CI checks out
full history. Scanner errors fail the check, and raw stdout/stderr is withheld.
Reports redact secret values completely.

The reviewed `.gitleaks.toml` permits only two exact public/synthetic strings and
narrow public metadata shapes for the generic-key heuristic. No test/source
directory is excluded, inline suppression comments are ignored, and no baseline
of accepted credential leaks is used. Review exception changes as security changes.

## Artifacts and reproduction

CI retains JUnit XML and coverage XML even on regression failure, plus redacted
security reports. Successful checks export OpenAPI, the frontend handoff, wheel,
sdist, package/lock SHA-256 identities, Git revision/dirty state, Python version,
migration revision, container image ID and sanitized local anchoring receipt.
Build metadata deliberately never serializes environment variables or Settings.

From the repository root with Python 3.12 and a disposable PostgreSQL 16 `_test`
database, run:

```powershell
$env:FIREWATCH_TEST_DATABASE_URL='postgresql+psycopg://test:test-password@127.0.0.1:5432/firewatch_test'
$env:FIREWATCH_OFFLINE_TESTS='1'
.\.venv\Scripts\python.exe -m pytest --cov=firewatch --cov-report=xml:artifacts/coverage.xml --junitxml=artifacts/test-results.xml
.\.venv\Scripts\python.exe scripts/check_locks.py
.\.venv\Scripts\python.exe scripts/scan_secrets.py
.\.venv\Scripts\python.exe -m build --no-isolation
.\.venv\Scripts\python.exe scripts/release_metadata.py
```

Install the security lock into a separate Python 3.12 environment, then run:

```text
python -m pip install --require-hashes -r requirements-security.lock
python -m pip_audit -r requirements-dev.lock --disable-pip --no-deps --format json --output artifacts/security/dependencies.json
python -m pip_audit -r requirements-security.lock --disable-pip --no-deps --format json --output artifacts/security/tooling.json
docker build --tag firewatch-ci .
docker run --rm --entrypoint firewatch firewatch-ci --help
```

The Dockerfile is a Phase 5.5 build target, with hashed dependencies, installed
wheel, migration/config files and UID/GID 10001. It copies no `.env`, raw data,
trained model or wallet. Compose services, persistent storage, database startup
ordering and full-stack acceptance remain Phase 5.6.

## Optional live provider check

The separate `Manual live provider smoke` workflow runs only on manual dispatch
with `confirm_live_calls=true`. Configure the `live-provider-smoke` GitHub
environment and its `FIRMS_MAP_KEY` secret. It makes one bounded region/day FIRMS
fetch (plus quota lookup) and one three-hour Open-Meteo request. It neither opens
a database nor trains/scores/publishes results. Raw responses live in a temporary
directory and are deleted; only fixed status fields are uploaded. Missing secrets
and provider failures fail the manual check. It is not a required PR check.

Local equivalent: supply `FIRMS_MAP_KEY` explicitly in the process environment,
then run `python -m scripts.live_provider_smoke --live`. The script does not read
the application's `.env`. Phase implementation does not implicitly authorize or
execute a live check, and normal tests verify its opt-in and redaction boundaries.

See [Phase 5.5 acceptance](phase-5.5-checklist.md) for verified local results and
the explicit hosted/container validation limits.
