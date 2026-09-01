# Q3750: Id colliding with an already whitelisted pool - callback fails

## Question
Can an unprivileged attacker choose an id that collides in the whitelist's key space with an existing legitimate pool, when the callback promise itself fails after deployment succeeded, breaking the invariant that each whitelisted entry corresponds to exactly one account, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Choose an id that collides in the whitelist's key space with an existing legitimate pool, when the callback promise itself fails after deployment succeeded.
- Invariant to test: Each whitelisted entry corresponds to exactly one account.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Unit test the whitelist key space.
