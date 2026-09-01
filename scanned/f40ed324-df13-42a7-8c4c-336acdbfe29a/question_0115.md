# Q0115: Foundation attached only when vesting is present - front-run

## Question
Can an unprivileged attacker create a vesting lockup shaped so `foundation_account` ends up absent or set to an account that cannot terminate it, by sending the transaction one block ahead of the legitimate creator, breaking the invariant that every vesting lockup has a working terminator, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Create a vesting lockup shaped so `foundation_account` ends up absent or set to an account that cannot terminate it, by sending the transaction one block ahead of the legitimate creator.
- Invariant to test: Every vesting lockup has a working terminator.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test the argument branches in `create`.
