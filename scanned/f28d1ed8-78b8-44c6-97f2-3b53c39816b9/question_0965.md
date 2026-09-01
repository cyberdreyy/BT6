# Q0965: Release schedule chosen by a third party - max-length name

## Question
Can an unprivileged attacker create a victim's lockup with a `release_duration` and `lockup_duration` that release much sooner or later than the grant intended, with a name at the account-id length limit so `format!` yields an over-long id, breaking the invariant that schedule fields come from the grantor, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Create a victim's lockup with a `release_duration` and `lockup_duration` that release much sooner or later than the grant intended, with a name at the account-id length limit so `format!` yields an over-long id.
- Invariant to test: Schedule fields come from the grantor.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Compare created terms against intended terms.
