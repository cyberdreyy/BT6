# Q0789: migrate_builtin_to_core_bpf grows memory without an enforced bound (mod.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `migrate_builtin_to_core_bpf` in `runtime/src/bank/builtins/core_bpf_migration/mod.rs` with a declared cost far below the real cost of the work requested, and grow the buffer `migrate_builtin_to_core_bpf` feeds without any eviction bound taking effect, so that the invariant "Every container this path writes into has an enforced capacity or eviction policy." breaks and the result is DoS?

## Target
- File/function: `runtime/src/bank/builtins/core_bpf_migration/mod.rs` -> `migrate_builtin_to_core_bpf()` (around line 224)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a declared cost far below the real cost of the work requested
- Exploit idea: Repeatedly drive `migrate_builtin_to_core_bpf` so a buffer, map, or cache it feeds grows without eviction, exhausting node memory below the cost the attacker pays.
- Invariant to test: Every container this path writes into has an enforced capacity or eviction policy.
- Expected Immunefi impact: DoS - remote resource exhaustion via non-RPC protocols (315-1,250 SOL)
- Fast validation: Stress the path and assert the container's size plateaus rather than growing linearly with attacker input.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
High. A single low-rate request or one websocket subscription from one client can consume memory, CPU, or file descriptors that grow with on-chain data instead of with an explicit bound, degrading the node without exceeding one call per CLUSTER_SLOT_TIME_TARGET / 2.
