# Phase 5.4 acceptance: reliable anchoring worker

- [x] Separate outbox worker and chain/signer PostgreSQL lock; scoring never signs.
- [x] Exact saved digest/alert key, destination/writer validation and gas/funds caps.
- [x] Signed bytes/hash/nonce commit before broadcast; restart checks receipts and
  reuses the same transaction after uncertain submission.
- [x] Queued/submitted/confirmed/failed states, safe errors, receipt/block metadata,
  configurable confirmations and continuing canonicality/reorg monitoring.
- [x] Distinct reverted/pending/RPC/funds/nonce/conflict/corruption results; bounded
  unsent retries and safe manual requeue without nonce replacement.
- [x] Operator anchor jobs follow outbox progress atomically. Independent queue
  capacity prevents a chain backlog from blocking refresh/replay admission/scheduling.
- [x] Real read-only RPC adapter uses recorded identities and canonical receipts;
  outages/reorgs never masquerade as a hash mismatch. APIs need no signing key.
- [x] Local-chain kind/identity/limitations are explicit. No public network is used.
- [x] Real PostgreSQL/Anvil smoke proves abrupt child-process crash recovery,
  confirmations, HTTP match, reorg recovery and evidence/publication isolation.
- [x] Dependency additions are hashed and existing package versions are constrained;
  no schema migration, ML artifact/promotion or application configuration change.

## Verification record

The [sanitized live receipt](phase-5.4-smoke.json) records a dedicated random test
signer, loopback Anvil deployment, real signed transaction, API verification and
reorg recovery in a disposable migrated PostgreSQL synthetic demo schema.
It does not claim real incident evaluation, a public-chain deployment or independent
notarization. The application database and local `.env` remain untouched.

Unit RPC/signing tests and PostgreSQL tests cover the bounded/error/read-only
boundary, preparation/broadcast/confirmation crashes, accepted-broadcast timeout,
concurrent signers, missing funds, gas caps, corruption, revert, reorg, CLI status/
retry and queue isolation. The complete regression passed **1,236 tests**, with
one Windows real-symlink skip, in **473.00 seconds** on PostgreSQL 16. The final
RPC/worker suite passed **45 tests**, including canonical block-hash-pinned reads
added after full-suite collection. The final operator/scoring/anchor integration
run passed **68 tests**, covering independent queue budgets and safe CLI retry.

Ruff lint/format (195 scoped Python files), strict mypy (104 source files),
scaffold/link/exclusion checks and Git whitespace checks passed. The hashed locks
agree at 71 runtime / 88 development packages; all 54 preexisting runtime versions
remain unchanged, with 17 added signing dependencies. Source/wheel builds and
runtime-only dependency consistency passed. The installed wheel reproduced local
signing/recovery and keyless read-adapter construction with settings loading,
network, database creation, model fitting and pickle access denied. New command
help works without startup configuration. Configured-secret scanning passed;
no schema migration or application `.env` change was made.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.verify_anchoring
.\.venv\Scripts\python.exe -m ruff check src tests scripts/contract_tools.py scripts/verify_anchoring.py
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe scripts/check_scaffold.py
.\.venv\Scripts\python.exe scripts/check_locks.py
.\.venv\Scripts\firewatch.exe anchor-worker --help
.\.venv\Scripts\firewatch.exe anchor-status --help
.\.venv\Scripts\firewatch.exe retry-anchor --help
```

One existing Windows real-symlink skip and upstream Starlette/httpx TestClient
deprecation warning may remain. Hosted CI, public testnet/mainnet confirmation
policy and valuable-fund custody are not asserted by local checks. No automatic
fee replacement, nonce skipping, registry deployment or independent scientific
validation is performed by the worker.

See the [operator and wire guide](durable-anchoring.md).
