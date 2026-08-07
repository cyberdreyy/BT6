# Q0918: calculate_points_for_tower crashes the process from one request (points.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `calculate_points_for_tower` in `runtime/src/inflation_rewards/points.rs` with a value large enough that an intermediate product overflows before the final divide, and make `calculate_points_for_tower` panic, abort, or block the runtime from that single request, so that the invariant "No single request can panic or block the process; bad parameters yield an error response." breaks and the result is RPC DoS/Crash?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `calculate_points_for_tower()` (around line 103)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a value large enough that an intermediate product overflows before the final divide
- Exploit idea: Send one request whose parameters make `calculate_points_for_tower` panic, abort, or block the runtime, taking down the validator rather than only the RPC thread.
- Invariant to test: No single request can panic or block the process; bad parameters yield an error response.
- Expected Immunefi impact: RPC DoS/Crash - single low-rate call from one client crashes or stalls the node (20-65 SOL)
- Fast validation: Fuzz the request parameters against `calculate_points_for_tower` and assert every case returns an error response and the process survives.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
