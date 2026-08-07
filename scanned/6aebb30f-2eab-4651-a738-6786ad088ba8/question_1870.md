# Q1870: get_precompile_signature_details accepts input it should reject (signature_details.rs)

## Question
Can an unprivileged attacker entering through a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair reach `get_precompile_signature_details` in `runtime-transaction/src/signature_details.rs` with input that makes the check pass on a value it later stops using, and have `get_precompile_signature_details` accept input that fails the property it is supposed to prove, so that the invariant "`get_precompile_signature_details` accepts exactly the valid set: no forged input passes and no valid input fails." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `get_precompile_signature_details()` (around line 61)
- Entrypoint: a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair
- Attacker controls: input that makes the check pass on a value it later stops using
- Exploit idea: Construct input that `get_precompile_signature_details` accepts although it fails the property it is supposed to prove (signature, proof, derivation, ownership).
- Invariant to test: `get_precompile_signature_details` accepts exactly the valid set: no forged input passes and no valid input fails.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Differential test `get_precompile_signature_details` against a reference implementation over random valid and mutated-invalid inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
