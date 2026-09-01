# Q0359: Full access key while a staking promise is in flight - exact unlock block

## Question
Can an unprivileged attacker pass `assert_no_staking_or_idle` during the window where a staking callback has not yet updated `deposit_amount`, so the locked computation is based on stale numbers, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that the key gate uses staking state that has settled, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Pass `assert_no_staking_or_idle` during the window where a staking callback has not yet updated `deposit_amount`, so the locked computation is based on stale numbers, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: The key gate uses staking state that has settled.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim key addition mid-promise.
