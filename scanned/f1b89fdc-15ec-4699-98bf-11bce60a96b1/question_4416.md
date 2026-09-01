# Q4416: Id validation bypassed by the format string - victim named as owner

## Question
Can an unprivileged attacker pass a `staking_pool_id` that passes `find('.').is_none()` and `is_valid_account_id` on the concatenated string yet yields an id the factory did not intend, naming a victim account as the owner of the created contract, breaking the invariant that the whitelisted id is exactly `<id>.<factory>`, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `staking-pool-factory/src/lib.rs` - `StakingPoolFactory::create_staking_pool / on_staking_pool_create`
- Entrypoint: `create_staking_pool(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `staking_pool_id`, `owner_id`, `stake_public_key`, `reward_fee_fraction`, the deposit and the gas
- Exploit idea: Pass a `staking_pool_id` that passes `find('.').is_none()` and `is_valid_account_id` on the concatenated string yet yields an id the factory did not intend, naming a victim account as the owner of the created contract.
- Invariant to test: The whitelisted id is exactly `<id>.<factory>`.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Unit test adversarial id strings against the derivation.
