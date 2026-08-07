# Q2664: free is not deterministic across nodes (pubkeys_ptr.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `free` in `scheduling-utils/src/pubkeys_ptr.rs` with an instruction sequence that re-enters the same code path within one transaction, and make the connection/stream quota accounted per source disagree with the streams actually admitted and served, so that the invariant "For identical committed state and feature set, `free` returns byte-identical results on every node." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `scheduling-utils/src/pubkeys_ptr.rs` -> `free()` (around line 62)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: an instruction sequence that re-enters the same code path within one transaction
- Exploit idea: Find input to `free` whose result depends on iteration order, map ordering, cache warmth, timing, or float/HashMap behaviour rather than only on committed state.
- Invariant to test: For identical committed state and feature set, `free` returns byte-identical results on every node.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Differential test: run `free` twice with shuffled input ordering and a cold vs warm cache; assert identical output.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
