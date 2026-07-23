# Q4121: get_transaction_lock unauthorized object ownership transition

## Question
Can an unprivileged attacker reach `get_transaction_lock` with crafted obj_ref, epoch_store and cause a non-owned, differently-owned, or stale-version object to be accepted as spendable or mutable, breaking Sui's ownership and transfer invariants?

## Target
- File/function: crates/sui-core/src/execution_cache/object_locks.rs::get_transaction_lock
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: obj_ref, epoch_store
- Exploit idea: Probe object ID, version, dynamic-field, capability, and ownership-graph assumptions for a path that treats attacker-unowned state as authorized.
- Invariant to test: Only the legitimate owner or explicitly authorized capability path may read, transfer, mutate, or destroy an owned object.
- Expected Immunefi impact: Critical — unauthorized use or transfer of owned objects leading to significant loss of funds.
- Fast validation: Use a local transaction with mismatched ownership or version state and verify whether the object can be moved or mutated anyway.
