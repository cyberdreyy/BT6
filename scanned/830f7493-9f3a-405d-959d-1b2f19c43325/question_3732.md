# Q3732: set_root_signal_receiver answers at the wrong slot, fork, or commitment (bank_forks_controller.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `set_root_signal_receiver` in `runtime/src/bank_forks_controller.rs` with a repeated operation that the code assumes happens at most once, and make the epoch boundary state computed by this node disagree with the state computed by a node that replayed the same blocks, so that the invariant "Finalized answers derive only from rooted slots on the canonical fork." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `runtime/src/bank_forks_controller.rs` -> `set_root_signal_receiver()` (around line 214)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a repeated operation that the code assumes happens at most once
- Exploit idea: Get `set_root_signal_receiver` to answer a finalized-commitment query from unrooted or wrong-fork state, so an integrator credits value on state that can still be rolled back.
- Invariant to test: Finalized answers derive only from rooted slots on the canonical fork.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Query across a fork with a slot that is confirmed but not rooted; assert the finalized response excludes it.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
