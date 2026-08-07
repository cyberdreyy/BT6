# Q3810: calculate_non_circulating_supply arithmetic overflows on reachable values (non_circulating_supply.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `calculate_non_circulating_supply` in `runtime/src/non_circulating_supply.rs` with a value large enough that an intermediate product overflows before the final divide, and make the arithmetic in `calculate_non_circulating_supply` overflow, wrap, or divide by zero, so that the invariant "All arithmetic on attacker-controlled values is checked or provably bounded." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `runtime/src/non_circulating_supply.rs` -> `calculate_non_circulating_supply()` (around line 19)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a value large enough that an intermediate product overflows before the final divide
- Exploit idea: Supply values that make `calculate_non_circulating_supply` overflow, so debug builds abort and release builds wrap into a nonsensical accounting value.
- Invariant to test: All arithmetic on attacker-controlled values is checked or provably bounded.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Proptest `calculate_non_circulating_supply` across full integer ranges; assert checked arithmetic and no wrap in release mode.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
