# Q5661: Foundation attached only when vesting is present - id derived from a victim account

## Question
Can an unprivileged attacker create a vesting lockup shaped so `foundation_account` ends up absent or set to an account that cannot terminate it, deriving the account id from a victim's account id, breaking the invariant that every vesting lockup has a working terminator, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Create a vesting lockup shaped so `foundation_account` ends up absent or set to an account that cannot terminate it, deriving the account id from a victim's account id.
- Invariant to test: Every vesting lockup has a working terminator.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the argument branches in `create`.
