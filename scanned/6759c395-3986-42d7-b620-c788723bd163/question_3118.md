# Q3118: notify_roots is not deterministic across nodes (rpc_subscriptions.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `notify_roots` in `rpc/src/rpc_subscriptions.rs` with an empty or single-element set at the boundary of the accumulation, and make the token amount and decimals reported disagree with the mint's real decimals and raw amount, so that the invariant "For identical committed state and feature set, `notify_roots` returns byte-identical results on every node." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `rpc/src/rpc_subscriptions.rs` -> `notify_roots()` (around line 727)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: an empty or single-element set at the boundary of the accumulation
- Exploit idea: Find input to `notify_roots` whose result depends on iteration order, map ordering, cache warmth, timing, or float/HashMap behaviour rather than only on committed state.
- Invariant to test: For identical committed state and feature set, `notify_roots` returns byte-identical results on every node.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Differential test: run `notify_roots` twice with shuffled input ordering and a cold vs warm cache; assert identical output.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
