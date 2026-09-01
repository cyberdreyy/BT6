# Q0337: Withdrawn tokens counted twice - exact unlock block

## Question
Can an unprivileged attacker make `on_withdraw_unvested_amount` credit `termination_withdrawn_tokens` for a transfer that failed, or fail to credit one that succeeded, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that `termination_withdrawn_tokens` equals the NEAR that actually reached the foundation, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Make `on_withdraw_unvested_amount` credit `termination_withdrawn_tokens` for a transfer that failed, or fail to credit one that succeeded, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: `termination_withdrawn_tokens` equals the NEAR that actually reached the foundation.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim both outcomes and check the field.
