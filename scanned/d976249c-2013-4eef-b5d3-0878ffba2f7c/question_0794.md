# Q794: check_system_overload settlement accounting gap

## Question
Can an unprivileged attacker reach `check_system_overload` with crafted tx_data, do_authority_overload_check and make one settlement layer apply a debit, credit, reservation, or refund without the corresponding counter-update, leaving exploitable residual value or stuck liabilities?

## Target
- File/function: crates/sui-core/src/authority.rs::check_system_overload
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: tx_data, do_authority_overload_check
- Exploit idea: Look for multi-step state transitions where a late abort or retry can desynchronize reservations, balances, or effect accounting.
- Invariant to test: Every debit, reservation, release, and refund must have an exactly paired state transition, even across retries and partial failure.
- Expected Immunefi impact: Critical if extractable, otherwise Medium for harmful contract behavior or locked value.
- Fast validation: Run local partial-failure sequences and compare reservation, balance, and effect tables before and after retry.
