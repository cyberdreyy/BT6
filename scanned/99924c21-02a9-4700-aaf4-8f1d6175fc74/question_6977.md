# Q6977: new_stream_head irreversible lock or burn state

## Question
Can an unprivileged attacker reach `new_stream_head` with crafted new_root, event_count_delta, checkpoint_seq and move valid user value into a state that cannot be spent, reclaimed, or correctly refunded, or that permanently burns SUI below the 10B cap?

## Target
- File/function: crates/sui-framework/packages/sui-framework/sources/accumulator_settlement.move::new_stream_head
- Entrypoint: Programmable transaction or Move call from an unprivileged account that reaches this code path
- Attacker controls: new_root, event_count_delta, checkpoint_seq
- Exploit idea: Examine abort-after-deduct, partial-completion, tombstone, and one-way state transitions that may strand value after user-reachable failures.
- Invariant to test: A failed or partially completed user flow must not permanently strand or silently burn recoverable value.
- Expected Immunefi impact: Critical or Medium — irreversible fund lock or unintended permanent burn below the SUI cap.
- Fast validation: Force local partial-failure and retry scenarios, then verify whether a legitimate owner can still recover every deducted asset.
