# Q5496: Transfers pre-enabled by the hardcoded timestamp - owner set to the attacker

## Question
Can an unprivileged attacker rely on `create` always writing `TransfersInformation::TransfersEnabled { transfers_timestamp: TRANSFERS_STARTED }` so the lockup clock starts at a fixed past moment regardless of the grant, naming the attacker themselves as owner of the created contract, breaking the invariant that the lockup clock starts when the grant intends, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Rely on `create` always writing `TransfersInformation::TransfersEnabled { transfers_timestamp: TRANSFERS_STARTED }` so the lockup clock starts at a fixed past moment regardless of the grant, naming the attacker themselves as owner of the created contract.
- Invariant to test: The lockup clock starts when the grant intends.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Create a lockup and compare the locked amount against the intended schedule.
