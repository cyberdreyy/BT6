# Q4143: Tracked set desynchronised from the whitelist - collides with tracked set

## Question
Can an unprivileged attacker make `staking_pool_account_ids` and the whitelist disagree, by failing after the insert but before or after the whitelist call, with an id colliding with an entry already in the factory's tracked set, breaking the invariant that the tracked set and the whitelist hold the same ids, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Make `staking_pool_account_ids` and the whitelist disagree, by failing after the insert but before or after the whitelist call, with an id colliding with an entry already in the factory's tracked set.
- Invariant to test: The tracked set and the whitelist hold the same ids.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim each failure point and compare both sets.
