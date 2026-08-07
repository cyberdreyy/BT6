# Q1172: less_than can be driven into unbounded work (dependency_tracker.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `less_than` in `runtime/src/dependency_tracker.rs` with arguments that drive the path into its error branch after side effects were applied, and make `less_than` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `less_than` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `runtime/src/dependency_tracker.rs` -> `less_than()` (around line 14)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Grow the attacker-controlled collection `less_than` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `less_than` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `less_than` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
