# Q3451: Repeat creation after a foundation removal - account created, init failed

## Question
Can an unprivileged attacker re-create a pool id the foundation previously removed from the whitelist, so the factory re-whitelists it without any review, in the case where the account is created but its `new` call fails, breaking the invariant that an id the foundation removed cannot be re-whitelisted by an unprivileged caller, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Re-create a pool id the foundation previously removed from the whitelist, so the factory re-whitelists it without any review, in the case where the account is created but its `new` call fails.
- Invariant to test: An id the foundation removed cannot be re-whitelisted by an unprivileged caller.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim removal then re-creation.
