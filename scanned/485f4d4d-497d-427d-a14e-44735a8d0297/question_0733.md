# Q0733: check_reserved_keys accepts input it should reject (bank.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `check_reserved_keys` in `runtime/src/bank.rs` with a payload that satisfies the cheap precondition but not the full check, and have `check_reserved_keys` accept input that fails the property it is supposed to prove, so that the invariant "`check_reserved_keys` accepts exactly the valid set: no forged input passes and no valid input fails." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime/src/bank.rs` -> `check_reserved_keys()` (around line 5580)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a payload that satisfies the cheap precondition but not the full check
- Exploit idea: Construct input that `check_reserved_keys` accepts although it fails the property it is supposed to prove (signature, proof, derivation, ownership).
- Invariant to test: `check_reserved_keys` accepts exactly the valid set: no forged input passes and no valid input fails.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Differential test `check_reserved_keys` against a reference implementation over random valid and mutated-invalid inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
