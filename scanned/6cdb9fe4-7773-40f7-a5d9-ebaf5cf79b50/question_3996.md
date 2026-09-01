# Q3996: Initial pool balance below the guarantee fund - repeat in same block

## Question
Can an unprivileged attacker arrange the created pool's balance so `new`'s `account_balance - STAKE_SHARE_PRICE_GUARANTEE_FUND` underflows or leaves a degenerate seed, by repeating the call with identical arguments in the same block, breaking the invariant that an initialised pool always has a positive seeded staked balance and share count, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Arrange the created pool's balance so `new`'s `account_balance - STAKE_SHARE_PRICE_GUARANTEE_FUND` underflows or leaves a degenerate seed, by repeating the call with identical arguments in the same block.
- Invariant to test: An initialised pool always has a positive seeded staked balance and share count.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Unit test `new` at that boundary.
