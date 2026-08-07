# Q2926: prune_unstaked_connections_and_add_new_connection can be driven into unbounded work (swqos.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `prune_unstaked_connections_and_add_new_connection` in `streamer/src/nonblocking/swqos.rs` with arguments that drive the path into its error branch after side effects were applied, and make `prune_unstaked_connections_and_add_new_connection` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `prune_unstaked_connections_and_add_new_connection` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `streamer/src/nonblocking/swqos.rs` -> `prune_unstaked_connections_and_add_new_connection()` (around line 258)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Grow the attacker-controlled collection `prune_unstaked_connections_and_add_new_connection` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `prune_unstaked_connections_and_add_new_connection` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `prune_unstaked_connections_and_add_new_connection` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
