# Q1420: close_user_certs_for_manual_epoch_close settlement accounting gap

## Question
Can an unprivileged attacker reach `close_user_certs_for_manual_epoch_close` with crafted lock_guard and make one settlement layer apply a debit, credit, reservation, or refund without the corresponding counter-update, leaving exploitable residual value or stuck liabilities?

## Target
- File/function: crates/sui-core/src/authority/authority_per_epoch_store.rs::close_user_certs_for_manual_epoch_close
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: lock_guard
- Exploit idea: Look for multi-step state transitions where a late abort or retry can desynchronize reservations, balances, or effect accounting.
- Invariant to test: Every debit, reservation, release, and refund must have an exactly paired state transition, even across retries and partial failure.
- Expected Immunefi impact: Critical if extractable, otherwise Medium for harmful contract behavior or locked value.
- Fast validation: Run local partial-failure sequences and compare reservation, balance, and effect tables before and after retry.
