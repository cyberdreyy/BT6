# Q1861: is_reserved grows memory without an enforced bound (lib.rs)

## Question
Can an unprivileged attacker entering through a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair reach `is_reserved` in `reserved-account-keys/src/lib.rs` with a request that stays one unit under the limit but repeats within a single transaction, and grow the buffer `is_reserved` feeds without any eviction bound taking effect, so that the invariant "Every container this path writes into has an enforced capacity or eviction policy." breaks and the result is DoS?

## Target
- File/function: `reserved-account-keys/src/lib.rs` -> `is_reserved()` (around line 80)
- Entrypoint: a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair
- Attacker controls: a request that stays one unit under the limit but repeats within a single transaction
- Exploit idea: Repeatedly drive `is_reserved` so a buffer, map, or cache it feeds grows without eviction, exhausting node memory below the cost the attacker pays.
- Invariant to test: Every container this path writes into has an enforced capacity or eviction policy.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Stress the path and assert the container's size plateaus rather than growing linearly with attacker input.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. A single low-rate request or one websocket subscription from one client can consume memory, CPU, or file descriptors that grow with on-chain data instead of with an explicit bound, degrading the node without exceeding one call per CLUSTER_SLOT_TIME_TARGET / 2.
