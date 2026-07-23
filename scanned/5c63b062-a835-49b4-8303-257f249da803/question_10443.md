# Q10443: get_early_execution_error unauthorized object ownership transition

## Question
Can an unprivileged attacker reach `get_early_execution_error` with crafted transaction_digest, input_objects, config_certificate_deny_set, funds_withdraw_status and cause a non-owned, differently-owned, or stale-version object to be accepted as spendable or mutable, breaking Sui's ownership and transfer invariants?

## Target
- File/function: crates/sui-types/src/execution_params.rs::get_early_execution_error
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: transaction_digest, input_objects, config_certificate_deny_set, funds_withdraw_status
- Exploit idea: Probe object ID, version, dynamic-field, capability, and ownership-graph assumptions for a path that treats attacker-unowned state as authorized.
- Invariant to test: Only the legitimate owner or explicitly authorized capability path may read, transfer, mutate, or destroy an owned object.
- Expected Immunefi impact: Critical — unauthorized use or transfer of owned objects leading to significant loss of funds.
- Fast validation: Use a local transaction with mismatched ownership or version state and verify whether the object can be moved or mutated anyway.
