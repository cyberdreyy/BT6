# Q0415: Hostile whitelist injected at creation - crafted name

## Question
Can an unprivileged attacker pass `whitelist_account_id` pointing at a contract the attacker controls, so the created lockup will approve any staking pool, with a `name` / `staking_pool_id` argument whose characters change the derived account path, breaking the invariant that the created lockup consults the canonical whitelist, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Pass `whitelist_account_id` pointing at a contract the attacker controls, so the created lockup will approve any staking pool, with a `name` / `staking_pool_id` argument whose characters change the derived account path.
- Invariant to test: The created lockup consults the canonical whitelist.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim creation with a hostile whitelist then select an arbitrary pool.
