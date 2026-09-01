# Q0187: Terminating state blocks all recovery - before unlock

## Question
Can an unprivileged attacker reach a terminating state where every method panics, including the ones meant to resolve it, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`, breaking the invariant that the terminating state never becomes unresolvable, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Reach a terminating state where every method panics, including the ones meant to resolve it, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`.
- Invariant to test: The terminating state never becomes unresolvable.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim the state and try every method.
