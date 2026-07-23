# Q4532: new_without_init dynamic-field or derived-object aliasing

## Question
Can an unprivileged attacker use crafted path, registry, max_type_length, remove_deprecated_tables to make `new_without_init` resolve the wrong dynamic field, derived object, table entry, or versioned record, so state is read or mutated under the wrong authority boundary?

## Target
- File/function: crates/sui-core/src/jsonrpc_index.rs::new_without_init
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: path, registry, max_type_length, remove_deprecated_tables
- Exploit idea: Search for key derivation, aliasing, or lookup mismatches that let one logical asset or capability overlap another.
- Invariant to test: Each dynamic field or derived object key must resolve to exactly one authority domain and never alias unrelated state.
- Expected Immunefi impact: Critical — unauthorized state mutation or asset movement through dynamic-field or derived-object confusion.
- Fast validation: Create colliding or near-colliding local keys and verify whether reads or writes can cross from one object namespace into another.
