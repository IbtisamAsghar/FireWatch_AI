# Durable anchoring worker: Phase 5.4

The separate anchor worker delivers immutable alert digests to the Phase 5.3
[registry](alert-registry.md). PostgreSQL outbox state survives process/RPC failure.
Scoring, publication and the ordinary pipeline worker never send transactions.
Anchor admission and refresh/replay admission have independent bounded queue
budgets, so stalled chain work cannot consume scoring/scheduler capacity.

## Configure and run

Provision a reviewed registry and its authorized signer separately. Keep the
private key in a server-side protected environment/secret file. API processes can
omit it: chain reads require RPC/chain/address, while only the anchor worker needs
`CHAIN_PRIVATE_KEY`. Never put that key in a browser or command-line argument.

```dotenv
CHAIN_ENABLED=true
CHAIN_RPC_URL=http://127.0.0.1:8545
CHAIN_ID=31337
ALERT_REGISTRY_ADDRESS=<deployed-address>
CHAIN_PRIVATE_KEY=<dedicated-authorized-signer-key>
CHAIN_NETWORK_KIND=local
CHAIN_CONFIRMATIONS=2
CHAIN_RPC_TIMEOUT_SECONDS=3
CHAIN_POLL_SECONDS=5
CHAIN_RECHECK_SECONDS=60
CHAIN_MAX_ATTEMPTS=20
CHAIN_GAS_LIMIT=100000
CHAIN_MAX_GAS_PRICE_WEI=200000000000
```

These are configuration examples, not actual provisioned credentials. The local
network kind requires a loopback RPC hostname. Testnet/mainnet are explicit
operator declarations; no public network or valuable funds were used in acceptance.
The confirmation count is a configured policy, not a guarantee of finality.

```powershell
.\.venv\Scripts\python.exe -m firewatch.anchor_worker
.\.venv\Scripts\firewatch.exe anchor-worker --once
.\.venv\Scripts\firewatch.exe anchor-status --job-id <operator-job-uuid>
.\.venv\Scripts\firewatch.exe anchor-status --outbox-id <outbox-uuid>
.\.venv\Scripts\firewatch.exe retry-anchor --outbox-id <failed-outbox-uuid>
```

Run one signer process initially. Each turn takes a PostgreSQL session advisory
lock keyed by chain ID and signer address, across all registry destinations.
Another worker with the same identity cannot sign concurrently. RPC calls occur
outside database transactions/row locks; short checkpoints fence the original
database backend PID. A lost physical connection cannot silently resume writing
under a new unlocked session. Process exit releases the session lock.

The worker processes only the exact configured `eip155:<chain-id>:<lowercase-address>`
destination. Changing settings never redirects old queued/signed work. Retain the
old destination/signer configuration to service historical deliveries.

## Transaction lifecycle and restart safety

1. Verify the original saved payload hash, chain ID, deployed code, registry
   writer and current commitment. Conflicting commitments fail explicitly.
2. Permit at most one unresolved signed transaction per chain/signer. Other
   destinations and ambiguous failed transactions also fence new nonce allocation.
   An externally pending signer nonce reports `CHAIN_SIGNER_BUSY`.
3. Check gas-price cap and balance, then sign a chain-ID-protected legacy EVM
   transaction locally using the locked `eth-account` implementation. The key is
   never sent to RPC. No fee replacement or automatic nonce skipping is performed.
4. Commit signed transaction bytes, deterministic hash, nonce, destination,
   signer, gas policy and evidence identity in `anchor_outbox.receipt_metadata`.
   Mark submitted and synchronize an associated operator job before broadcasting.
5. Broadcast those exact bytes. On restart, check the saved hash's receipt and
   transaction visibility first. A timeout after node acceptance cannot create a
   new nonce/transaction: retries reuse the stored bytes/hash. Persisted signed
   transactions are re-signed from their pinned fields and compared before use.
6. Check successful receipt status, transaction/destination/sender identity,
   canonical block hash, confirmation count and the registry's digest at that
   receipt block. Save block hash/height and `network_kind` in the separate anchor
   record. Confirm the outbox and operator job atomically.

Digest reads use the receipt's exact block hash and `requireCanonical=true`
([EIP-1898](https://eips.ethereum.org/EIPS/eip-1898)), so a reorg between receipt
checking and state lookup cannot silently change the block being queried. RPC
providers without this capability report unavailable; there is no weaker
block-number fallback.

The raw signed transaction is private delivery state, not a private key, but is a
rebroadcast capability. It is never exposed by API projections or the status CLI.
It remains available for crash/reorg recovery. Do not delete/reset it to force a
new nonce. The signer must not concurrently be used by external tools/services.

`attempts` counts worker turns. Consecutive errors use bounded exponential retry
delays (up to 300 seconds); successful/pending checkpoints reset the error count.
Unsent retryable failures stop after `CHAIN_MAX_ATTEMPTS` consecutive errors.
Signer-busy waits consume no failure budget. Ambiguous signed broadcasts remain
tracked indefinitely rather than being abandoned and allowing conflicting nonces.

## States and operational recovery

| State/result | Meaning and action |
|---|---|
| `queued` | No transaction prepared; awaiting an attempt or retry. |
| `submitted` | Hash/bytes are durable; broadcast, mining or confirmations may still be pending. |
| `confirmed` | Canonical successful receipt and configured confirmations matched the evidence. |
| `failed` | A terminal condition or exhausted unsent retries needs operator attention. |
| `CHAIN_INSUFFICIENT_FUNDS` | Fund only the intended signer/network, then resume/retry. |
| `CHAIN_FEE_LIMIT` | Current gas price exceeds the configured spending cap. |
| `CHAIN_RPC_UNAVAILABLE` | Transport/HTTP failure; original evidence is unaffected. |
| `CHAIN_TRANSACTION_REVERTED` | Canonical receipt status is zero; nonce consumption is recorded. |
| `CHAIN_NONCE_CONFLICT` | The saved nonce appears consumed without its expected receipt; investigate instead of skipping it. |
| `CHAIN_DELIVERY_CORRUPT` | Stored signing pins/bytes no longer reproduce the transaction; no broadcast occurs. |
| `CHAIN_WRITER_MISMATCH` / `CHAIN_ID_MISMATCH` | Signer or RPC configuration differs from the intended destination. |
| `CHAIN_DIGEST_CONFLICT` | The chain commitment differs from the saved evidence. |

`anchor-status` returns IDs, destination, status, safe error code, attempts,
transaction hash, block metadata and confirmations. `--job-id` resolves the
private outbox identity from an admitted anchor job. The retry command takes the
same signer lock, clears an exhausted failure budget and retains any signed
transaction identity. It refuses known reverted/consumed-nonce records. A retry
does not bypass identity, evidence or contract checks and cannot repair a genuine
conflict. Correct the cause first; never rewrite saved alert evidence or its hash.

Receipt monitoring continues after confirmation. Missing/orphaned receipts demote
the anchor/outbox/job to submitted and clear stale block metadata; the same saved
transaction may be rebroadcast. Confirmed chain reads independently recheck
canonicality, so an orphaned/reset chain never remains a false `anchor_match`
between worker polls. Lost local contract/deployment state is unavailable and
requires explicit restoration; the worker does not redeploy contracts.

## Read-only verification

When chain reads are enabled, the API installs `RpcAnchorReader` without making
startup RPC requests or loading a signing account. It reads only the configured
recorded chain/contract, receipt/block identity, confirmation count and the opaque
alert key's stored digest. RPC is bounded by timeouts and a 1 MiB response limit.
Malformed/outage/reorg responses yield `anchor_unavailable`, not a mismatch.
An unconfirmed receipt remains pending. A valid different retrieved digest is a
mismatch. GETs never sign, submit, queue work or update database state.

Payload, optional artifact and chain results remain independent. Health/readiness
do not probe RPC. A chain failure never changes immutable alert bytes or prevents
pipeline publication. Local chains are resettable demonstrations; a matching
digest does not prove incident reality or model validity.

## Acceptance and reproduction

Install the locked Python dependencies and [native contract tools](../contracts/README.md).
Set `FIREWATCH_TEST_DATABASE_URL` to a disposable PostgreSQL database ending `_test`:

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_anchoring
.\.venv\Scripts\python.exe -m pytest tests/unit/test_chain_rpc.py tests/integration/test_anchor_delivery.py -q
```

The live smoke starts/stops fresh loopback Anvil, uses a random test-only signer,
deploys the registry and creates only a generated synthetic PostgreSQL demo
schema. A child signer abruptly exits after its pre-broadcast commit; the parent
resumes the same hash, verifies HTTP confirmation behavior, simulates/replays a
reorg and checks RPC outage isolation. It preserves all original alert bytes and
run counts, drops its demo schema and stops its node. No application `.env` or
database is used as a fallback. The sanitized receipt is written to
`artifacts/phase-5.4-smoke.json`; a reviewed local run is recorded in
[Phase 5.4 smoke](phase-5.4-smoke.json).

See [acceptance](phase-5.4-checklist.md). Sources:
[Ethereum JSON-RPC](https://ethereum.org/developers/docs/apis/json-rpc/) and
[eth-account transaction signing](https://eth-account.readthedocs.io/en/stable/eth_account.html).
