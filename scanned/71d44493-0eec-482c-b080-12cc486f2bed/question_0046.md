# Q0046: unzip is not deterministic across nodes (config.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `unzip` in `rpc-client-types/src/config.rs` with arguments that drive the path into its error branch after side effects were applied, and make the token amount and decimals reported disagree with the mint's real decimals and raw amount, so that the invariant "For identical committed state and feature set, `unzip` returns byte-identical results on every node." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `rpc-client-types/src/config.rs` -> `unzip()` (around line 102)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Find input to `unzip` whose result depends on iteration order, map ordering, cache warmth, timing, or float/HashMap behaviour rather than only on committed state.
- Invariant to test: For identical committed state and feature set, `unzip` returns byte-identical results on every node.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Differential test: run `unzip` twice with shuffled input ordering and a cold vs warm cache; assert identical output.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
