# Q2872: Status machine re-entered from a stale callback - inflated deposit_amount

## Question
Can an unprivileged attacker let a late callback set a termination status that contradicts the current one, unlocking the account, while `staking_information.deposit_amount` exceeds what the pool really owes this lockup, breaking the invariant that the termination status only advances through its intended sequence, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Let a late callback set a termination status that contradicts the current one, unlocking the account, while `staking_information.deposit_amount` exceeds what the pool really owes this lockup.
- Invariant to test: The termination status only advances through its intended sequence.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim out-of-order callbacks.
