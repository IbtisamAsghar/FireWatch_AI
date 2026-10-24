# AlertRegistry: Phase 5.3

The optional registry stores exact evidence SHA-256 commitments from one immutable
writer, rejects conflicting overwrites and makes identical retries no-ops.
Coordinates, weather and model contents remain off-chain.

Run from the repository root with Python 3.12:

```powershell
.\.venv\Scripts\python.exe scripts/contract_tools.py install
.\.venv\Scripts\python.exe scripts/contract_tools.py test
.\.venv\Scripts\python.exe scripts/contract_tools.py smoke
```

The installer verifies official checksum pins and installs native Foundry 1.8.6 /
Solidity 0.8.30 in `.local/foundry`. Windows/Linux x86_64 are supported. No Docker,
WSL, Node package, global binary installation, forge-std or Python lock change is
required. Compilation/tests use the explicit compiler with `--offline`.

The smoke starts Anvil on an ephemeral loopback-only port with zero default
accounts, generates/funds a fresh dedicated test signer, deploys the registry and
anchors the committed synthetic evidence example. It checks events/readback and
retry/revert behavior, then stops Anvil automatically. Keys stay in subprocess
memory/environment; raw signing traces are suppressed and generated files are
checked for the key. The default sanitized receipt is
`artifacts/phase-5.3-smoke.json`. Its addresses describe a disposable historical
test, not a running deployment or public notarization.

Sources: [contract](src/AlertRegistry.sol), [Forge tests](test/AlertRegistry.t.sol),
[deployment](script/DeployAlertRegistry.s.sol),
[local smoke](script/AnchorLocalEvidence.s.sol), [ABI](abi/AlertRegistry.json) and
[toolchain pins](toolchain.json). See [wire/operator guide](../docs/alert-registry.md)
and [acceptance](../docs/phase-5.3-checklist.md).

The ordinary backend requires no chain tools. These commands do not change
`.env`, application database evidence/outbox or the active scorer. Durable
submission, RPC adapters, receipts and signer operations remain Phase 5.4.
