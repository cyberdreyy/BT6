# Q3932: Refund routed to the wrong predecessor - repeat in same block

## Question
Can an unprivileged attacker exploit `on_lockup_create` carrying `predecessor_account_id` from the outer call so the refund lands somewhere the payer did not choose, by repeating the call with identical arguments in the same block, breaking the invariant that the refund goes to the account that paid, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Exploit `on_lockup_create` carrying `predecessor_account_id` from the outer call so the refund lands somewhere the payer did not choose, by repeating the call with identical arguments in the same block.
- Invariant to test: The refund goes to the account that paid.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a failure path and check the recipient.
