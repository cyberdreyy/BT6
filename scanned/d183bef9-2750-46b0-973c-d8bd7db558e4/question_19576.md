# Q19576: from_resolution_table unauthorized object ownership transition

## Question
Can an unprivileged attacker reach `from_resolution_table` with crafted resolution_table and cause a non-owned, differently-owned, or stale-version object to be accepted as spendable or mutable, breaking Sui's ownership and transfer invariants?

## Target
- File/function: sui-execution/latest/sui-adapter/src/static_programmable_transactions/linkage/resolved_linkage.rs::from_resolution_table
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: resolution_table
- Exploit idea: Probe object ID, version, dynamic-field, capability, and ownership-graph assumptions for a path that treats attacker-unowned state as authorized.
- Invariant to test: Only the legitimate owner or explicitly authorized capability path may read, transfer, mutate, or destroy an owned object.
- Expected Immunefi impact: Critical — unauthorized use or transfer of owned objects leading to significant loss of funds.
- Fast validation: Use a local transaction with mismatched ownership or version state and verify whether the object can be moved or mutated anyway.
