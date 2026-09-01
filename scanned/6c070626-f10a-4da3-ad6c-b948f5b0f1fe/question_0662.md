# Q0662: Staked balance under-reported for the unstake step - release_duration = 0

## Question
Can an unprivileged attacker have the pool under-report the staked balance so the unstake for termination covers less than the deficit, on a lockup created with `release_duration = Some(0)`, breaking the invariant that the unstaked amount covers the whole deficit, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Have the pool under-report the staked balance so the unstake for termination covers less than the deficit, on a lockup created with `release_duration = Some(0)`.
- Invariant to test: The unstaked amount covers the whole deficit.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim an under-reporting pool.
