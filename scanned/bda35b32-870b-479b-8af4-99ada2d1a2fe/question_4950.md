# Q4950: Victim's derived address squatted - hostile poll named

## Question
Can an unprivileged attacker create a lockup at `hex::encode(&env::sha256(owner_account_id.as_bytes())[..20])` for an owner whose real grant has not been created yet, fixing the terms before the grantor can, naming a transfer poll contract the attacker deployed, breaking the invariant that the lockup living at an owner's derived address carries the terms that owner's grantor chose, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Create a lockup at `hex::encode(&env::sha256(owner_account_id.as_bytes())[..20])` for an owner whose real grant has not been created yet, fixing the terms before the grantor can, naming a transfer poll contract the attacker deployed.
- Invariant to test: The lockup living at an owner's derived address carries the terms that owner's grantor chose.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim: attacker creates first, then the legitimate creation is attempted and compared.
