# Phase 5.3 acceptance: optional Solidity registry

- [x] Local evidence/verification acceptance precedes chain work; official event
  requirements/rules were checked and linked in the operator guide.
- [x] Minimal immutable-writer registry stores bytes32 keys/exact digests and emits
  an event, without coordinates/weather/model contents on-chain.
- [x] Backend helpers and local readback prove direct saved SHA-256 encoding;
  model version remains bound inside hashed off-chain evidence.
- [x] Unauthorized calls/retries, zero writer/key/digest and conflicting
  overwrites revert. Identical authorized retries are no-ops without events.
- [x] Forge tests cover authorization, duplicates, reads, events and fuzzed inputs;
  compiled ABI must match its committed copy.
- [x] Released Foundry 1.8.6 / Solidity 0.8.30 are checksum-pinned for native
  Windows/Linux. Compiler/EVM/optimizer choices are explicit.
- [x] Loopback-only Anvil deployment uses a fresh dedicated signer, real local
  signed transactions, no default accounts and test-only funding.
- [x] Anvil stops automatically; keys stay out of argv/receipts and generated
  outputs are scanned. Local-chain independence limits are documented.
- [x] CI includes pinned tooling, tests, ABI parity and local smoke with a sanitized
  artifact. Hosted execution is not asserted by local success.

## Verification

**16 Forge tests passed**, including three fuzz tests with 256 cases each. The
registry source achieved **100% lines/statements/branches/functions** (17/17,
18/18, 6/6 and 3/3). Deployment scripts are exercised by the Anvil smoke, outside
that registry-only coverage claim. Coverage instrumentation emits source-anchor
warnings; ordinary optimized compilation/tests succeed.

The local smoke deployed the registry, anchored the committed synthetic canonical
example, verified event topics/exact stored digest, submitted an identical signed
retry with no event, and rejected unauthorized/conflicting calls. See the
[sanitized receipt](phase-5.3-smoke.json).

The Python wire/tooling and adjacent evidence/API regression passed **94 tests**,
with one existing Windows real-symlink skip. Mocked symlink/junction guard tests
pass. The existing upstream Starlette/httpx TestClient deprecation warning remains.
No full backend regression rerun is claimed for this isolated phase; Phase 5.2's
complete PostgreSQL acceptance record remains unchanged.

Ruff/formatting, strict mypy, scaffold/links/exclusions, unchanged locks and secret
scanning are checked alongside this phase. Runtime-only wheel checks exercise the
pure helpers with settings/network/DB/fitting/pickle access denied. Toolchain
installation is independent of Python dependency locks.

## Reproduce

```powershell
.\.venv\Scripts\python.exe scripts/contract_tools.py install
.\.venv\Scripts\python.exe scripts/contract_tools.py test
.\.venv\Scripts\python.exe scripts/contract_tools.py smoke
.\.venv\Scripts\python.exe -m pytest tests/unit/test_chain_contract.py tests/unit/test_contract_tools.py tests/unit/test_evidence.py tests/unit/test_artifact_audit.py tests/unit/test_api_contract.py -q
```

The smoke changes no application evidence/outbox, `.env`, model approval or active
scorer. It claims neither real incident validation nor public notarization.
Durable transaction/receipt/RPC integration remains Phase 5.4.
