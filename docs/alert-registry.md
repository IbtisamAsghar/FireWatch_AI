# AlertRegistry contract and local deployment

Phase 5.3 implements `alert-registry-v1`. The
[source](../contracts/src/AlertRegistry.sol), [ABI](../contracts/abi/AlertRegistry.json)
and [pure backend helpers](../src/firewatch/evidence/anchor.py) freeze the boundary
for the later anchoring worker. Core scoring/evidence need no chain.

## Contract interface

```solidity
constructor(address authorizedWriter)
writer() external view returns (address)
anchor(bytes32 alertKey, bytes32 digest) external returns (bool created)
evidenceDigest(bytes32 alertKey) external view returns (bytes32)
event EvidenceAnchored(bytes32 indexed alertKey, bytes32 indexed evidenceDigest,
                       address indexed writer)
```

The writer must be nonzero and is immutable. The deployer is not automatically
authorized. Every write, including retries, requires that writer. There is no
owner bypass, delete, overwrite, upgrade/proxy, rotation or fund-handling API.
Writer loss/compromise requires a new registry destination; historical records
retain their original chain/contract identity.

Zero keys/digests are rejected, so a zero getter result means no entry. A first
write returns true and emits one event. An identical authorized retry returns
false with no event. A different digest for the same key reverts with
`ConflictingDigest` and preserves the original. Different keys may share a digest.
Unknown function selectors revert.

## Frozen bytes32 encoding

```text
alertKey = 0x + SHA256(UTF8("firewatch-alert-key-v1") || 0x00 || UUID.bytes).hex()
```

`UUID.bytes` is the UUID's 16 network-order bytes, not text or `bytes_le`. The
golden key for `00000000-0000-4000-8000-000000000001` is:

```text
0xb693673cfc1ede01f3c0b6f1207e6fe878759a4d08c1c61703c91d3e52980f45
```

`evidenceDigest` is the saved `alerts.original_hash` decoded directly into 32
bytes: `0x` plus its 64 lowercase hexadecimal digits for ABI/RPC. The helper checks
the exact original bytes first. Never substitute keccak/SHA3, SHA-256-of-hex-text,
reserialized JSON or a redacted projection. EVM selectors use their normal
encoding without changing the SHA-256 value. The smoke compares the event's indexed
digest and getter byte-for-byte with the backend value.

Model version, location, weather and provenance remain inside immutable off-chain
evidence; its digest binds those fields. Legacy evidence can be committed without
re-encoding. Only opaque keys, digests and writer/event identity are on-chain.

## Native installation and local deployment

Run the commands in the [contracts README](../contracts/README.md). The
[toolchain manifest](../contracts/toolchain.json) pins released
[Foundry 1.8.6](https://github.com/foundry-rs/foundry/releases/tag/v1.8.6) and
[Solidity 0.8.30](https://github.com/argotorg/solidity/releases/tag/v0.8.30), with
official release-asset and [solc-bin](https://github.com/ethereum/solc-bin)
SHA-256 values. Build settings fix Cancun EVM, optimizer 200 runs and disabled
CBOR metadata. There are no external Solidity libraries. The helper checks
installed binary hashes/version strings and committed ABI parity before acceptance.

Installation downloads official binaries; tests compile offline. The smoke uses
only local RPC afterward: fresh chain ID 31337, explicit `127.0.0.1` binding, zero
default accounts and a new signer funded only through Anvil's development RPC.
It deploys and signs two real local transactions. Unauthorized/conflicting calls
use local `eth_call`; Forge independently tests the revert paths. No public Anvil
private key or valuable funds are used. No application database is accessed and
the application's `.env` remains unchanged.

The deployment script reads `FIREWATCH_DEPLOY_PRIVATE_KEY`,
`FIREWATCH_DEPLOY_CHAIN_ID` and `FIREWATCH_REGISTRY_WRITER` from process environment.
It checks the connected chain ID before broadcasting. The local anchoring script
additionally requires chain ID 31337; the helper supplies its `FIREWATCH_SMOKE_*`
inputs. Keys never enter command-line arguments or receipts. Raw failed signing
output is suppressed because traces may contain secrets. For build/test diagnosis,
run Forge tests, which do not use signing keys.

Anvil stops in `finally`, including failures. Generated contract outputs,
cache/broadcast directories, `.local` and ordinary artifacts are Git-ignored.
Outputs are scanned for the ephemeral key before recording a successful smoke.
The committed [receipt](phase-5.3-smoke.json) contains only public test identities,
hashes and outcomes. It does not identify an available running chain.

## Limits and next phase

A commitment does not prove a fire is real, validate ML quality, authenticate
satellite measurements or guarantee off-chain file availability. Anvil can be
reset: it is a local demonstration, not independent public notarization. Later
receipts must record `network_kind=local`, chain ID and contract address.

Automatic outbox processing, RPC reading, signer custody/recovery, nonce handling,
transaction/receipt persistence, confirmations, reorg policy and retries remain
Phase 5.4. This phase marks no application alert as anchored.

## Official event review

Checked on 24 October 2026: the [official requirements](https://ieee-climatechain-hack.devpost.com/)
describe climate solutions using blockchain, AI and data-driven approaches and
include a Climate Data & Environmental Monitoring track. Submission items include
a prototype/demo and code repository; that page specifies no particular public
chain or public-testnet deployment. The [rules page](https://ieee-climatechain-hack.devpost.com/rules)
was also checked. This informs implementation scope, not organizer approval.
Recheck current official requirements before actual submission.

See [Phase 5.3 acceptance](phase-5.3-checklist.md).
