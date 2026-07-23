# Q15287: verify dynamic-field or derived-object aliasing

## Question
Can an unprivileged attacker use crafted function_context to make `verify` resolve the wrong dynamic field, derived object, table entry, or versioned record, so state is read or mutated under the wrong authority boundary?

## Target
- File/function: external-crates/move/crates/move-bytecode-verifier/src/jump_table_usage_verifier.rs::verify
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: function_context
- Exploit idea: Search for key derivation, aliasing, or lookup mismatches that let one logical asset or capability overlap another.
- Invariant to test: Each dynamic field or derived object key must resolve to exactly one authority domain and never alias unrelated state.
- Expected Immunefi impact: Critical — unauthorized state mutation or asset movement through dynamic-field or derived-object confusion.
- Fast validation: Create colliding or near-colliding local keys and verify whether reads or writes can cross from one object namespace into another.
