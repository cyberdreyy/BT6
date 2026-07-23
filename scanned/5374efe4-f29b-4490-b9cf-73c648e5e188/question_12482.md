# Q12482: funds_withdrawal irreversible lock or burn state

## Question
Can an unprivileged attacker reach `funds_withdrawal` with crafted arg and move valid user value into a state that cannot be spent, reclaimed, or correctly refunded, or that permanently burns SUI below the 10B cap?

## Target
- File/function: crates/sui-types/src/programmable_transaction_builder.rs::funds_withdrawal
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: arg
- Exploit idea: Examine abort-after-deduct, partial-completion, tombstone, and one-way state transitions that may strand value after user-reachable failures.
- Invariant to test: A failed or partially completed user flow must not permanently strand or silently burn recoverable value.
- Expected Immunefi impact: Critical or Medium — irreversible fund lock or unintended permanent burn below the SUI cap.
- Fast validation: Force local partial-failure and retry scenarios, then verify whether a legitimate owner can still recover every deducted asset.
