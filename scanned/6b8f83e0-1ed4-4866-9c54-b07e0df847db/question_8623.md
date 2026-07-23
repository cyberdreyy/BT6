# Q8623: load_object_by_id unauthorized object ownership transition

## Question
Can an unprivileged attacker reach `load_object_by_id` with crafted runtime_object_resolver, version_bound, id and cause a non-owned, differently-owned, or stale-version object to be accepted as spendable or mutable, breaking Sui's ownership and transfer invariants?

## Target
- File/function: crates/sui-types/src/accumulator_root.rs::load_object_by_id
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: runtime_object_resolver, version_bound, id
- Exploit idea: Probe object ID, version, dynamic-field, capability, and ownership-graph assumptions for a path that treats attacker-unowned state as authorized.
- Invariant to test: Only the legitimate owner or explicitly authorized capability path may read, transfer, mutate, or destroy an owned object.
- Expected Immunefi impact: Critical — unauthorized use or transfer of owned objects leading to significant loss of funds.
- Fast validation: Use a local transaction with mismatched ownership or version state and verify whether the object can be moved or mutated anyway.
