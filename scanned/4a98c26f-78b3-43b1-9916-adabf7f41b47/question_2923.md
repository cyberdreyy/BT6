# Q2923: on_stream_finished can be driven into unbounded work (swqos.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `on_stream_finished` in `streamer/src/nonblocking/swqos.rs` with an ordering of instructions that leaves partial state from an earlier failure, and make `on_stream_finished` iterate over an attacker-sized set far past the per-slot budget, so that the invariant "Iteration count in `on_stream_finished` is bounded by a constant or by a value the transaction pays for." breaks and the result is Liveness / Loss of Availability?

## Target
- File/function: `streamer/src/nonblocking/swqos.rs` -> `on_stream_finished()` (around line 490)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: an ordering of instructions that leaves partial state from an earlier failure
- Exploit idea: Grow the attacker-controlled collection `on_stream_finished` iterates so a single transaction pins the replay thread far past the slot budget.
- Invariant to test: Iteration count in `on_stream_finished` is bounded by a constant or by a value the transaction pays for.
- Expected Immunefi impact: Liveness / Loss of Availability - consensus halts and requires human intervention (1,250-5,000 SOL)
- Fast validation: Benchmark `on_stream_finished` as the attacker-controlled size grows; assert runtime stays under the per-slot budget.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
