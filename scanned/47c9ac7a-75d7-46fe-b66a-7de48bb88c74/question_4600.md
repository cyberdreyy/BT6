# Q4600: Pool selected through a whitelist the owner chose - no staking pool selected

## Question
Can an unprivileged attacker select a pool that only passes `on_whitelist_is_whitelisted` because `staking_pool_whitelist_account_id` points at a contract the attacker deployed at creation time, while `staking_information` is `None`, so the deposit term drops out of the balance calculation, breaking the invariant that the whitelist consulted is the canonical one the grant was created against, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Select a pool that only passes `on_whitelist_is_whitelisted` because `staking_pool_whitelist_account_id` points at a contract the attacker deployed at creation time, while `staking_information` is `None`, so the deposit term drops out of the balance calculation.
- Invariant to test: The whitelist consulted is the canonical one the grant was created against.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim a lockup created with a hostile whitelist and select an arbitrary pool.
