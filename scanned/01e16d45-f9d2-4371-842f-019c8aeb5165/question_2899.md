# Q2899: cache_new_connection crashes the process from one request (simple_qos.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `cache_new_connection` in `streamer/src/nonblocking/simple_qos.rs` with an ordering of instructions that leaves partial state from an earlier failure, and make `cache_new_connection` panic, abort, or block the runtime from that single request, so that the invariant "No single request can panic or block the process; bad parameters yield an error response." breaks and the result is RPC DoS/Crash?

## Target
- File/function: `streamer/src/nonblocking/simple_qos.rs` -> `cache_new_connection()` (around line 182)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: an ordering of instructions that leaves partial state from an earlier failure
- Exploit idea: Send one request whose parameters make `cache_new_connection` panic, abort, or block the runtime, taking down the validator rather than only the RPC thread.
- Invariant to test: No single request can panic or block the process; bad parameters yield an error response.
- Expected Immunefi impact: RPC DoS/Crash - single low-rate call from one client crashes or stalls the node (20-65 SOL)
- Fast validation: Fuzz the request parameters against `cache_new_connection` and assert every case returns an error response and the process survives.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
