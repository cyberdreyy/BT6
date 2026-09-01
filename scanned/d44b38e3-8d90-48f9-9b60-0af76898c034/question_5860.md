# Q5860: Account creation with the balance already present - repeated across many ids

## Question
Can an unprivileged attacker target an id whose account already exists with a balance, so `create_account` fails but the transfer or the tracked insert still happened, repeating the creation across many derived ids, breaking the invariant that a failed creation leaves no state and no NEAR behind, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Target an id whose account already exists with a balance, so `create_account` fails but the transfer or the tracked insert still happened, repeating the creation across many derived ids.
- Invariant to test: A failed creation leaves no state and no NEAR behind.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim creation onto an existing account.
