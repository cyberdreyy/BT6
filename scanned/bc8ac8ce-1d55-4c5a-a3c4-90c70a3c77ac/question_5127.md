# Q5127: Transfer racing an inbound staking withdrawal - lockup_duration = 0

## Question
Can an unprivileged attacker transfer in the window between `withdraw_from_staking_pool` crediting the account and `on_staking_pool_withdraw` lowering `deposit_amount`, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that the same NEAR is never counted both as a pool deposit and as an account balance, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Transfer in the window between `withdraw_from_staking_pool` crediting the account and `on_staking_pool_withdraw` lowering `deposit_amount`, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: The same NEAR is never counted both as a pool deposit and as an account balance.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim the interleaving and reconcile.
