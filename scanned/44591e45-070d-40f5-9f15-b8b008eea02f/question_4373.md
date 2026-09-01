# Q4373: Min deposit boundary leaves an unusable lockup - collides with tracked set

## Question
Can an unprivileged attacker attach exactly `MIN_ATTACHED_BALANCE` so the created lockup cannot cover its own storage after `lockup_amount` is fixed, with an id colliding with an entry already in the factory's tracked set, breaking the invariant that a created lockup can always pay for its storage and still honour its schedule, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Attach exactly `MIN_ATTACHED_BALANCE` so the created lockup cannot cover its own storage after `lockup_amount` is fixed, with an id colliding with an entry already in the factory's tracked set.
- Invariant to test: A created lockup can always pay for its storage and still honour its schedule.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Create at the boundary and attempt a transfer.
