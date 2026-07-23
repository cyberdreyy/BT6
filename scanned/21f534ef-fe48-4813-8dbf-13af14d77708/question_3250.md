# Q3250: assert_not_forked settlement accounting gap

## Question
Can an unprivileged attacker reach `assert_not_forked` with crafted checkpoint, tx_digest, expected_digest, actual_effects_digest and make one settlement layer apply a debit, credit, reservation, or refund without the corresponding counter-update, leaving exploitable residual value or stuck liabilities?

## Target
- File/function: crates/sui-core/src/checkpoints/checkpoint_executor/utils.rs::assert_not_forked
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: checkpoint, tx_digest, expected_digest, actual_effects_digest
- Exploit idea: Look for multi-step state transitions where a late abort or retry can desynchronize reservations, balances, or effect accounting.
- Invariant to test: Every debit, reservation, release, and refund must have an exactly paired state transition, even across retries and partial failure.
- Expected Immunefi impact: Critical if extractable, otherwise Medium for harmful contract behavior or locked value.
- Fast validation: Run local partial-failure sequences and compare reservation, balance, and effect tables before and after retry.
