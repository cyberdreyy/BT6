# Q2336: insert_root_state_hash unauthorized object ownership transition

## Question
Can an unprivileged attacker reach `insert_root_state_hash` with crafted epoch, last_checkpoint_of_epoch, hash and cause a non-owned, differently-owned, or stale-version object to be accepted as spendable or mutable, breaking Sui's ownership and transfer invariants?

## Target
- File/function: crates/sui-core/src/authority/authority_store_tables.rs::insert_root_state_hash
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: epoch, last_checkpoint_of_epoch, hash
- Exploit idea: Probe object ID, version, dynamic-field, capability, and ownership-graph assumptions for a path that treats attacker-unowned state as authorized.
- Invariant to test: Only the legitimate owner or explicitly authorized capability path may read, transfer, mutate, or destroy an owned object.
- Expected Immunefi impact: Critical — unauthorized use or transfer of owned objects leading to significant loss of funds.
- Fast validation: Use a local transaction with mismatched ownership or version state and verify whether the object can be moved or mutated anyway.
