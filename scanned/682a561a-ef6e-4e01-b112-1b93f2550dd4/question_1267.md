# Q1267: migrate_legacy_hardlinks arithmetic overflows on reachable values (snapshot_utils.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `migrate_legacy_hardlinks` in `runtime/src/snapshot_utils.rs` with a value that makes the limit computation itself overflow into a larger allowance, and make the arithmetic in `migrate_legacy_hardlinks` overflow, wrap, or divide by zero, so that the invariant "All arithmetic on attacker-controlled values is checked or provably bounded." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `runtime/src/snapshot_utils.rs` -> `migrate_legacy_hardlinks()` (around line 1338)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a value that makes the limit computation itself overflow into a larger allowance
- Exploit idea: Supply values that make `migrate_legacy_hardlinks` overflow, so debug builds abort and release builds wrap into a nonsensical accounting value.
- Invariant to test: All arithmetic on attacker-controlled values is checked or provably bounded.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Proptest `migrate_legacy_hardlinks` across full integer ranges; assert checked arithmetic and no wrap in release mode.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
