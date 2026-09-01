# Q3354: Gas split leaves `new` unfinished - account created, init failed

## Question
Can an unprivileged attacker attach gas so the deploy succeeds but `new` runs out, leaving a deployed contract with no state and the callback treating it as failure, in the case where the account is created but its `new` call fails, breaking the invariant that a deployed lockup is always initialised or fully rolled back, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Attach gas so the deploy succeeds but `new` runs out, leaving a deployed contract with no state and the callback treating it as failure, in the case where the account is created but its `new` call fails.
- Invariant to test: A deployed lockup is always initialised or fully rolled back.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim minimal gas and inspect the created account.
