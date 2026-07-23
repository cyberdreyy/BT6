# Q458: build_add_tokens_on_sui_transaction irreversible lock or burn state

## Question
Can an unprivileged attacker reach `build_add_tokens_on_sui_transaction` with crafted client_address, gas_object_ref, action, bridge_object_arg and move valid user value into a state that cannot be spent, reclaimed, or correctly refunded, or that permanently burns SUI below the 10B cap?

## Target
- File/function: crates/sui-bridge/src/sui_transaction_builder.rs::build_add_tokens_on_sui_transaction
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: client_address, gas_object_ref, action, bridge_object_arg
- Exploit idea: Examine abort-after-deduct, partial-completion, tombstone, and one-way state transitions that may strand value after user-reachable failures.
- Invariant to test: A failed or partially completed user flow must not permanently strand or silently burn recoverable value.
- Expected Immunefi impact: Critical or Medium — irreversible fund lock or unintended permanent burn below the SUI cap.
- Fast validation: Force local partial-failure and retry scenarios, then verify whether a legitimate owner can still recover every deducted asset.
