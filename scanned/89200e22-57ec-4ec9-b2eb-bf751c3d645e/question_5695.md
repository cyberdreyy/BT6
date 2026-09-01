# Q5695: Owner_id set to an attacker account - id derived from a victim account

## Question
Can an unprivileged attacker create a pool naming the attacker as `owner_id` and rely on the whitelist to make it appear endorsed to lockup contracts, deriving the account id from a victim's account id, breaking the invariant that whitelisting implies nothing beyond what the factory verified, and lockups do not treat it as endorsement of the owner, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Create a pool naming the attacker as `owner_id` and rely on the whitelist to make it appear endorsed to lockup contracts, deriving the account id from a victim's account id.
- Invariant to test: Whitelisting implies nothing beyond what the factory verified, and lockups do not treat it as endorsement of the owner.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim a lockup selecting an attacker-owned whitelisted pool.
