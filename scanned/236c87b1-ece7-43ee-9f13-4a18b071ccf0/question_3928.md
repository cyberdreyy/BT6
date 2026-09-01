# Q3928: Unselect with a hidden residual deposit - same receipt as poll flip

## Question
Can an unprivileged attacker satisfy `unselect_staking_pool`'s `deposit_amount == 0` assertion while NEAR is still sitting in the pool, then select another pool and double count, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`, breaking the invariant that unselecting is only possible when the lockup has no claim left at the pool, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Satisfy `unselect_staking_pool`'s `deposit_amount == 0` assertion while NEAR is still sitting in the pool, then select another pool and double count, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`.
- Invariant to test: Unselecting is only possible when the lockup has no claim left at the pool.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim unselect with residual balance and reconcile.
