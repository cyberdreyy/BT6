# Q4205: Deposit refunded and kept - collides with tracked set

## Question
Can an unprivileged attacker make the `new` call fail after the account was created and funded, so `on_lockup_create` refunds `attached_deposit` while the created account keeps the transferred NEAR, with an id colliding with an entry already in the factory's tracked set, breaking the invariant that refunded deposit plus NEAR left in the created account equals the attached deposit, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Make the `new` call fail after the account was created and funded, so `on_lockup_create` refunds `attached_deposit` while the created account keeps the transferred NEAR, with an id colliding with an entry already in the factory's tracked set.
- Invariant to test: Refunded deposit plus NEAR left in the created account equals the attached deposit.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim a failing `new` and sum both sides.
