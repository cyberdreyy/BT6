# Q5401: Gas floor leaves the pool uninitialised but whitelisted - hostile whitelist named

## Question
Can an unprivileged attacker attach gas so the deploy succeeds, `new` runs out, and the callback still treats creation as successful, naming a whitelist contract the attacker deployed, breaking the invariant that whitelisting only follows a completed initialisation, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Attach gas so the deploy succeeds, `new` runs out, and the callback still treats creation as successful, naming a whitelist contract the attacker deployed.
- Invariant to test: Whitelisting only follows a completed initialisation.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim minimal gas and inspect both sides.
