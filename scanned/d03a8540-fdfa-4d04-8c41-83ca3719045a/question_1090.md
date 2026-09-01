# Q1090: 20-byte address prefix collision - unusable derived id

## Question
Can an unprivileged attacker find two `owner_account_id` values whose sha256 prefixes collide in the truncated 20 bytes used for the account id, with a name that makes the derived id an implicit-account or otherwise unusable form, breaking the invariant that distinct owners always map to distinct lockup accounts, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Find two `owner_account_id` values whose sha256 prefixes collide in the truncated 20 bytes used for the account id, with a name that makes the derived id an implicit-account or otherwise unusable form.
- Invariant to test: Distinct owners always map to distinct lockup accounts.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Unit test the derivation for collision resistance at 20 bytes.
