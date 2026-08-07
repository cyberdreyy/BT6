# Q3085: rewards_only crashes the process from one request (config.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `rewards_only` in `rpc-client-types/src/config.rs` with values chosen so the arithmetic saturates, wraps, or rounds toward the attacker, and make `rewards_only` panic, abort, or block the runtime from that single request, so that the invariant "No single request can panic or block the process; bad parameters yield an error response." breaks and the result is RPC DoS/Crash?

## Target
- File/function: `rpc-client-types/src/config.rs` -> `rewards_only()` (around line 283)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: values chosen so the arithmetic saturates, wraps, or rounds toward the attacker
- Exploit idea: Send one request whose parameters make `rewards_only` panic, abort, or block the runtime, taking down the validator rather than only the RPC thread.
- Invariant to test: No single request can panic or block the process; bad parameters yield an error response.
- Expected Immunefi impact: RPC DoS/Crash - single low-rate call from one client crashes or stalls the node (20-65 SOL)
- Fast validation: Fuzz the request parameters against `rewards_only` and assert every case returns an error response and the process survives.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
