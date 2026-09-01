# Q1340: Vesting hash supplied by the creator - unusable derived id

## Question
Can an unprivileged attacker create the lockup with a `VestingScheduleOrHash::VestingHash` commitment the attacker knows the preimage of, with a name that makes the derived id an implicit-account or otherwise unusable form, breaking the invariant that the vesting commitment is the grantor's, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Create the lockup with a `VestingScheduleOrHash::VestingHash` commitment the attacker knows the preimage of, with a name that makes the derived id an implicit-account or otherwise unusable form.
- Invariant to test: The vesting commitment is the grantor's.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim creation with a known-preimage hash.
