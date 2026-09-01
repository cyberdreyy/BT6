# Q2315: Existing account blocks or absorbs creation - no deposit

## Question
Can an unprivileged attacker occupy the derived address first so the create batch fails at a step that still moves NEAR, attaching zero deposit on the path that does not check one, breaking the invariant that a failed creation leaves no NEAR stranded, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Occupy the derived address first so the create batch fails at a step that still moves NEAR, attaching zero deposit on the path that does not check one.
- Invariant to test: A failed creation leaves no NEAR stranded.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim creation onto an existing account.
