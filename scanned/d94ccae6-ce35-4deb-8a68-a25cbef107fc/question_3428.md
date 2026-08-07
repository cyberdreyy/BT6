# Q3428: previous_completed_index answers at the wrong slot, fork, or commitment (blockstore_meta.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `previous_completed_index` in `ledger/src/blockstore_meta.rs` with a lookup whose result is cached and then invalidated by the attacker's own write, and make the blockstore's view of a slot's contents disagree with the bank state derived from that slot, so that the invariant "Finalized answers derive only from rooted slots on the canonical fork." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `ledger/src/blockstore_meta.rs` -> `previous_completed_index()` (around line 102)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a lookup whose result is cached and then invalidated by the attacker's own write
- Exploit idea: Get `previous_completed_index` to answer a finalized-commitment query from unrooted or wrong-fork state, so an integrator credits value on state that can still be rolled back.
- Invariant to test: Finalized answers derive only from rooted slots on the canonical fork.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Query across a fork with a slot that is confirmed but not rooted; assert the finalized response excludes it.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
