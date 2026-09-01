# Q1037: Owner drains before termination completes - release_duration ~ u64::MAX

## Question
Can an unprivileged attacker move NEAR out in the window between the terminating state being set and `termination_withdraw` running, on a lockup created with `release_duration` close to `u64::MAX`, breaking the invariant that no NEAR leaves the account between termination and its withdrawal, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Move NEAR out in the window between the terminating state being set and `termination_withdraw` running, on a lockup created with `release_duration` close to `u64::MAX`.
- Invariant to test: No NEAR leaves the account between termination and its withdrawal.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim the race and reconcile.
