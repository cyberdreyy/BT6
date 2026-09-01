# Q5882: Pool pre-funded before init - repeated across many ids

## Question
Can an unprivileged attacker send NEAR to the derived pool id before the factory's batch so `new` seeds `total_staked_balance` and `total_stake_shares` from an inflated balance, repeating the creation across many derived ids, breaking the invariant that seeded totals come only from the factory's transfer, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Send NEAR to the derived pool id before the factory's batch so `new` seeds `total_staked_balance` and `total_stake_shares` from an inflated balance, repeating the creation across many derived ids.
- Invariant to test: Seeded totals come only from the factory's transfer.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim pre-funding then creation and inspect the seed.
