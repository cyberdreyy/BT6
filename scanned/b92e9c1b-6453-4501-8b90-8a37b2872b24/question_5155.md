# Q5155: build_transaction_outputs settlement accounting gap

## Question
Can an unprivileged attacker reach `build_transaction_outputs` with crafted transaction, effects, inner_temporary_store, unchanged_loaded_runtime_objects and make one settlement layer apply a debit, credit, reservation, or refund without the corresponding counter-update, leaving exploitable residual value or stuck liabilities?

## Target
- File/function: crates/sui-core/src/transaction_outputs.rs::build_transaction_outputs
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: transaction, effects, inner_temporary_store, unchanged_loaded_runtime_objects
- Exploit idea: Look for multi-step state transitions where a late abort or retry can desynchronize reservations, balances, or effect accounting.
- Invariant to test: Every debit, reservation, release, and refund must have an exactly paired state transition, even across retries and partial failure.
- Expected Immunefi impact: Critical if extractable, otherwise Medium for harmful contract behavior or locked value.
- Fast validation: Run local partial-failure sequences and compare reservation, balance, and effect tables before and after retry.
