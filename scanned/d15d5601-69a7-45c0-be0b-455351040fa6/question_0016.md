# Q0016: Whitelisting an account that is not the deployed pool - front-run

## Question
Can an unprivileged attacker reach `on_staking_pool_create`'s `ext_whitelist::add_staking_pool` for an account whose deployed code or state is not the pool the factory believed it created, by sending the transaction one block ahead of the legitimate creator, breaking the invariant that every whitelisted id runs the factory-deployed pool code with the factory's arguments, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Reach `on_staking_pool_create`'s `ext_whitelist::add_staking_pool` for an account whose deployed code or state is not the pool the factory believed it created, by sending the transaction one block ahead of the legitimate creator.
- Invariant to test: Every whitelisted id runs the factory-deployed pool code with the factory's arguments.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim a divergence between deployment and whitelisting.
