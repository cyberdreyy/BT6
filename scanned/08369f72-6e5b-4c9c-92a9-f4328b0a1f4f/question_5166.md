# Q5166: Repeated creations racing in one block - hostile poll named

## Question
Can an unprivileged attacker issue several `create` calls for the same owner in one block so the tracked state and the deployed account disagree, naming a transfer poll contract the attacker deployed, breaking the invariant that one owner id yields exactly one lockup account, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Issue several `create` calls for the same owner in one block so the tracked state and the deployed account disagree, naming a transfer poll contract the attacker deployed.
- Invariant to test: One owner id yields exactly one lockup account.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim concurrent creations.
