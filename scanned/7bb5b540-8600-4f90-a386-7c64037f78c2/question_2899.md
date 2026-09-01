# Q2899: Whitelist call fails, pool stays tracked - gas floor

## Question
Can an unprivileged attacker let the whitelist call fail while the factory has already removed nothing and reported success, attaching just enough prepaid gas that the `new` call runs out mid-initialisation, breaking the invariant that a pool is tracked only if it is whitelisted, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Let the whitelist call fail while the factory has already removed nothing and reported success, attaching just enough prepaid gas that the `new` call runs out mid-initialisation.
- Invariant to test: A pool is tracked only if it is whitelisted.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a failing whitelist call.
