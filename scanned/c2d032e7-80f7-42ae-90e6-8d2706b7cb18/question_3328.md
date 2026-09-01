# Q3328: Termination withdrawal capped by a manipulated balance - same receipt as poll flip

## Question
Can an unprivileged attacker keep `get_account_balance()` low at the moment `termination_withdraw` computes `min(unvested, balance)` so only part of the unvested NEAR is recovered, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`, breaking the invariant that the full unvested amount is recovered before the owner regains control, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Keep `get_account_balance()` low at the moment `termination_withdraw` computes `min(unvested, balance)` so only part of the unvested NEAR is recovered, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`.
- Invariant to test: The full unvested amount is recovered before the owner regains control.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a low balance at that instant.
