# Q0226: get_leader_tpus cost scales with on-chain data, not with an enforced bound (tpu_info.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `get_leader_tpus` in `send-transaction-service/src/tpu_info.rs` with a lookup whose result is cached and then invalidated by the attacker's own write, and make one call to `get_leader_tpus` walk an attacker-sized on-chain structure with no parameter bound stopping it, so that the invariant "Per-request work is bounded by explicit limits, not by attacker-authored data size." breaks and the result is RPC DoS/Crash?

## Target
- File/function: `send-transaction-service/src/tpu_info.rs` -> `get_leader_tpus()` (around line 15)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a lookup whose result is cached and then invalidated by the attacker's own write
- Exploit idea: Author on-chain data so a single in-scope-rate call to `get_leader_tpus` walks an attacker-sized structure, with no parameter limit stopping it.
- Invariant to test: Per-request work is bounded by explicit limits, not by attacker-authored data size.
- Expected Immunefi impact: RPC DoS/Crash - single low-rate call from one client crashes or stalls the node (20-65 SOL)
- Fast validation: Grow the on-chain structure and measure one call's time/allocations; assert they plateau.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
