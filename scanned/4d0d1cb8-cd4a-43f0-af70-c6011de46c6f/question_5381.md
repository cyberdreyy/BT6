# Q5381: transfer_status_not_found unauthorized object ownership transition

## Question
Can an unprivileged attacker reach `transfer_status_not_found` with crafted message bytes, proof fields, amount, recipient, nonce, and chain-domain values and cause a non-owned, differently-owned, or stale-version object to be accepted as spendable or mutable, breaking Sui's ownership and transfer invariants?

## Target
- File/function: crates/sui-framework/packages/bridge/sources/bridge.move::transfer_status_not_found
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: message bytes, proof fields, amount, recipient, nonce, and chain-domain values
- Exploit idea: Probe object ID, version, dynamic-field, capability, and ownership-graph assumptions for a path that treats attacker-unowned state as authorized.
- Invariant to test: Only the legitimate owner or explicitly authorized capability path may read, transfer, mutate, or destroy an owned object.
- Expected Immunefi impact: Critical — unauthorized use or transfer of owned objects leading to significant loss of funds.
- Fast validation: Use a local transaction with mismatched ownership or version state and verify whether the object can be moved or mutated anyway.
