# Q0185: Withdraw more than the pool owes - before unlock

## Question
Can an unprivileged attacker withdraw an amount derived from an inflated `deposit_amount`, so `saturating_sub` silently floors and the accounting loses the difference, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`, breaking the invariant that `deposit_amount` after a withdrawal equals the previous value minus the NEAR actually returned, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Withdraw an amount derived from an inflated `deposit_amount`, so `saturating_sub` silently floors and the accounting loses the difference, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`.
- Invariant to test: `deposit_amount` after a withdrawal equals the previous value minus the NEAR actually returned.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim an over-withdrawal and check the field.
