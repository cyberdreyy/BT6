# Q1163: prune_non_rooted accepts input it should reject (bank_forks.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `prune_non_rooted` in `runtime/src/bank_forks.rs` with an empty or single-element set at the boundary of the accumulation, and have `prune_non_rooted` accept input that fails the property it is supposed to prove, so that the invariant "`prune_non_rooted` accepts exactly the valid set: no forged input passes and no valid input fails." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime/src/bank_forks.rs` -> `prune_non_rooted()` (around line 697)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: an empty or single-element set at the boundary of the accumulation
- Exploit idea: Construct input that `prune_non_rooted` accepts although it fails the property it is supposed to prove (signature, proof, derivation, ownership).
- Invariant to test: `prune_non_rooted` accepts exactly the valid set: no forged input passes and no valid input fails.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Differential test `prune_non_rooted` against a reference implementation over random valid and mutated-invalid inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
