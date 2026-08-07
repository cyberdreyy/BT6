# Q0920: null_tracer can be driven into unbounded work (points.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `null_tracer` in `runtime/src/inflation_rewards/points.rs` with an instruction sequence that re-enters the same code path within one transaction, and make `null_tracer` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `null_tracer` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `null_tracer()` (around line 64)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: an instruction sequence that re-enters the same code path within one transaction
- Exploit idea: Grow the attacker-controlled collection `null_tracer` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `null_tracer` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `null_tracer` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
