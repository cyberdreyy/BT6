# Q1028: purge_old_snapshot_archives answers at the wrong slot, fork, or commitment (snapshot_utils.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `purge_old_snapshot_archives` in `runtime/src/snapshot_utils.rs` with state that is committed on one fork and then observed from another, and make the status cache entry deduping a signature disagree with the signatures actually committed in that slot, so that the invariant "Finalized answers derive only from rooted slots on the canonical fork." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `runtime/src/snapshot_utils.rs` -> `purge_old_snapshot_archives()` (around line 1603)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: state that is committed on one fork and then observed from another
- Exploit idea: Get `purge_old_snapshot_archives` to answer a finalized-commitment query from unrooted or wrong-fork state, so an integrator credits value on state that can still be rolled back.
- Invariant to test: Finalized answers derive only from rooted slots on the canonical fork.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Query across a fork with a slot that is confirmed but not rooted; assert the finalized response excludes it.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
