# Q1442: remove_by_inner_key_if grows memory without an enforced bound (secondary.rs)

## Question
Can an unprivileged attacker entering through ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for reach `remove_by_inner_key_if` in `accounts-db/src/accounts_index/secondary.rs` with a repeated operation that the code assumes happens at most once, and grow the buffer `remove_by_inner_key_if` feeds without any eviction bound taking effect, so that the invariant "Every container this path writes into has an enforced capacity or eviction policy." breaks and the result is DoS?

## Target
- File/function: `accounts-db/src/accounts_index/secondary.rs` -> `remove_by_inner_key_if()` (around line 220)
- Entrypoint: ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for
- Attacker controls: a repeated operation that the code assumes happens at most once
- Exploit idea: Repeatedly drive `remove_by_inner_key_if` so a buffer, map, or cache it feeds grows without eviction, exhausting node memory below the cost the attacker pays.
- Invariant to test: Every container this path writes into has an enforced capacity or eviction policy.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Stress the path and assert the container's size plateaus rather than growing linearly with attacker input.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. A single low-rate request or one websocket subscription from one client can consume memory, CPU, or file descriptors that grow with on-chain data instead of with an explicit bound, degrading the node without exceeding one call per CLUSTER_SLOT_TIME_TARGET / 2.
