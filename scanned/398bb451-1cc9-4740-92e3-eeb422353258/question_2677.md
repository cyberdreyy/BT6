# Q2677: only_one_contained crashes the process from one request (thread_aware_account_locks.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `only_one_contained` in `scheduling-utils/src/thread_aware_account_locks.rs` with arguments that drive the path into its error branch after side effects were applied, and make `only_one_contained` panic, abort, or block the runtime from that single request, so that the invariant "No single request can panic or block the process; bad parameters yield an error response." breaks and the result is RPC DoS/Crash?

## Target
- File/function: `scheduling-utils/src/thread_aware_account_locks.rs` -> `only_one_contained()` (around line 413)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Send one request whose parameters make `only_one_contained` panic, abort, or block the runtime, taking down the validator rather than only the RPC thread.
- Invariant to test: No single request can panic or block the process; bad parameters yield an error response.
- Expected Immunefi impact: RPC DoS/Crash - single low-rate call from one client crashes or stalls the node (20-65 SOL)
- Fast validation: Fuzz the request parameters against `only_one_contained` and assert every case returns an error response and the process survives.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
