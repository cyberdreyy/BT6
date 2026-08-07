# Q3178: parse_metadata_pointer_instruction accepts input it should reject (metadata_pointer.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `parse_metadata_pointer_instruction` in `transaction-status/src/parse_token/extension/metadata_pointer.rs` with a field ordering or duplicate field that the decoder tolerates but the consumer does not, and have `parse_metadata_pointer_instruction` accept input that fails the property it is supposed to prove, so that the invariant "`parse_metadata_pointer_instruction` accepts exactly the valid set: no forged input passes and no valid input fails." breaks and the result is Loss of Funds?

## Target
- File/function: `transaction-status/src/parse_token/extension/metadata_pointer.rs` -> `parse_metadata_pointer_instruction()` (around line 9)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a field ordering or duplicate field that the decoder tolerates but the consumer does not
- Exploit idea: Construct input that `parse_metadata_pointer_instruction` accepts although it fails the property it is supposed to prove (signature, proof, derivation, ownership).
- Invariant to test: `parse_metadata_pointer_instruction` accepts exactly the valid set: no forged input passes and no valid input fails.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Differential test `parse_metadata_pointer_instruction` against a reference implementation over random valid and mutated-invalid inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged RPC or pubsub request can mutate bank, blockstore, cache, or fork-choice state that consensus later depends on, or make the node treat an unrooted slot as rooted.
