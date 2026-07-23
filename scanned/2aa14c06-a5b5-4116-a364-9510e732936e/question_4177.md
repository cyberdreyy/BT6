# Q4177: settle_address_funds irreversible lock or burn state

## Question
Can an unprivileged attacker reach `settle_address_funds` with crafted settlement and move valid user value into a state that cannot be spent, reclaimed, or correctly refunded, or that permanently burns SUI below the 10B cap?

## Target
- File/function: crates/sui-core/src/execution_scheduler/execution_scheduler_impl.rs::settle_address_funds
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: settlement
- Exploit idea: Examine abort-after-deduct, partial-completion, tombstone, and one-way state transitions that may strand value after user-reachable failures.
- Invariant to test: A failed or partially completed user flow must not permanently strand or silently burn recoverable value.
- Expected Immunefi impact: Critical or Medium — irreversible fund lock or unintended permanent burn below the SUI cap.
- Fast validation: Force local partial-failure and retry scenarios, then verify whether a legitimate owner can still recover every deducted asset.
