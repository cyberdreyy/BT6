# Q2587: Pool initialised with a hostile fee fraction - oversized deposit

## Question
Can an unprivileged attacker create a pool whose `reward_fee_fraction` passes `assert_valid` but takes the entire reward from delegators, attaching a very large deposit that the failure path must refund, breaking the invariant that a whitelisted pool's parameters are within the bounds the whitelist implies, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Create a pool whose `reward_fee_fraction` passes `assert_valid` but takes the entire reward from delegators, attaching a very large deposit that the failure path must refund.
- Invariant to test: A whitelisted pool's parameters are within the bounds the whitelist implies.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Create a 100% fee pool and check it is whitelisted.
