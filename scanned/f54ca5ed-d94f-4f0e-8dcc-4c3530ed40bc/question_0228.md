# Q0228: refresh_recent_peers can be driven into unbounded work (tpu_info.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `refresh_recent_peers` in `send-transaction-service/src/tpu_info.rs` with an ordering of instructions that leaves partial state from an earlier failure, and make `refresh_recent_peers` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `refresh_recent_peers` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `send-transaction-service/src/tpu_info.rs` -> `refresh_recent_peers()` (around line 6)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: an ordering of instructions that leaves partial state from an earlier failure
- Exploit idea: Grow the attacker-controlled collection `refresh_recent_peers` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `refresh_recent_peers` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `refresh_recent_peers` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
