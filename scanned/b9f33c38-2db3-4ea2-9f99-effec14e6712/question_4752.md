# Q4752: Deposit kept while the failure path refunds - attacker-run dependency

## Question
Can an unprivileged attacker make `new` fail after the account was created and funded, so the refund and the funded account both retain NEAR, pointing the created contract at a whitelist, poll or pool contract the attacker deployed, breaking the invariant that refund plus NEAR left at the created account equals the deposit, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Make `new` fail after the account was created and funded, so the refund and the funded account both retain NEAR, pointing the created contract at a whitelist, poll or pool contract the attacker deployed.
- Invariant to test: Refund plus NEAR left at the created account equals the deposit.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim a failing init and sum both sides.
