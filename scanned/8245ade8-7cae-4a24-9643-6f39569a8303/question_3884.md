# Q3884: new_from_dir can be driven into unbounded work (snapshot_utils.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `new_from_dir` in `runtime/src/snapshot_utils.rs` with an ordering of instructions that leaves partial state from an earlier failure, and make `new_from_dir` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `new_from_dir` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `runtime/src/snapshot_utils.rs` -> `new_from_dir()` (around line 126)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: an ordering of instructions that leaves partial state from an earlier failure
- Exploit idea: Grow the attacker-controlled collection `new_from_dir` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `new_from_dir` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `new_from_dir` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
