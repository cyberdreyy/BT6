# Q2622: ed25519_verify accepts input it should reject (sigverify.rs)

## Question
Can an unprivileged attacker entering through raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port reach `ed25519_verify` in `perf/src/sigverify.rs` with a boundary value exactly on the accept/reject edge of the predicate, and have `ed25519_verify` accept input that fails the property it is supposed to prove, so that the invariant "`ed25519_verify` accepts exactly the valid set: no forged input passes and no valid input fails." breaks and the result is Loss of Funds?

## Target
- File/function: `perf/src/sigverify.rs` -> `ed25519_verify()` (around line 108)
- Entrypoint: raw QUIC/UDP packets and transaction batches sent by an unstaked client to the leader's public TPU port
- Attacker controls: a boundary value exactly on the accept/reject edge of the predicate
- Exploit idea: Construct input that `ed25519_verify` accepts although it fails the property it is supposed to prove (signature, proof, derivation, ownership).
- Invariant to test: `ed25519_verify` accepts exactly the valid set: no forged input passes and no valid input fails.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Differential test `ed25519_verify` against a reference implementation over random valid and mutated-invalid inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
