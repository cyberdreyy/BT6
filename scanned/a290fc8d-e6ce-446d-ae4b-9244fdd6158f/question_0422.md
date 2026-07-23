# Q422: get_token_transfer_action_onchain_status_until_success dynamic-field or derived-object aliasing

## Question
Can an unprivileged attacker use crafted source_chain_id, seq_number to make `get_token_transfer_action_onchain_status_until_success` resolve the wrong dynamic field, derived object, table entry, or versioned record, so state is read or mutated under the wrong authority boundary?

## Target
- File/function: crates/sui-bridge/src/sui_client.rs::get_token_transfer_action_onchain_status_until_success
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: source_chain_id, seq_number
- Exploit idea: Search for key derivation, aliasing, or lookup mismatches that let one logical asset or capability overlap another.
- Invariant to test: Each dynamic field or derived object key must resolve to exactly one authority domain and never alias unrelated state.
- Expected Immunefi impact: Critical — unauthorized state mutation or asset movement through dynamic-field or derived-object confusion.
- Fast validation: Create colliding or near-colliding local keys and verify whether reads or writes can cross from one object namespace into another.
