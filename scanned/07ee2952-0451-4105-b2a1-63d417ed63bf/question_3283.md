# Q3283: Stake_public_key that cannot stake - account created, init failed

## Question
Can an unprivileged attacker supply a `stake_public_key` for which the pool's initial `internal_restake` always fails, leaving a whitelisted but unusable pool that still accepts deposits, in the case where the account is created but its `new` call fails, breaking the invariant that a whitelisted pool can always return delegator funds, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Supply a `stake_public_key` for which the pool's initial `internal_restake` always fails, leaving a whitelisted but unusable pool that still accepts deposits, in the case where the account is created but its `new` call fails.
- Invariant to test: A whitelisted pool can always return delegator funds.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Create such a pool and try depositing and withdrawing.
