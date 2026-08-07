# Q0230: update_key answers at the wrong slot, fork, or commitment (transaction_client.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `update_key` in `send-transaction-service/src/transaction_client.rs` with a repeated operation that the code assumes happens at most once, and make the transaction status recorded at commit disagree with the status returned by the status query, so that the invariant "Finalized answers derive only from rooted slots on the canonical fork." breaks and the result is Consensus/Safety Violation?

## Target
- File/function: `send-transaction-service/src/transaction_client.rs` -> `update_key()` (around line 124)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a repeated operation that the code assumes happens at most once
- Exploit idea: Get `update_key` to answer a finalized-commitment query from unrooted or wrong-fork state, so an integrator credits value on state that can still be rolled back.
- Invariant to test: Finalized answers derive only from rooted slots on the canonical fork.
- Expected Immunefi impact: Consensus/Safety Violation - honest nodes commit different state, bank-hash mismatch or fork (3,125-12,500 SOL)
- Fast validation: Query across a fork with a slot that is confirmed but not rooted; assert the finalized response excludes it.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. A single unprivileged JSON-RPC or pubsub request, issued once by one client, can panic, abort, or deadlock the whole validator process rather than only its RPC service.
