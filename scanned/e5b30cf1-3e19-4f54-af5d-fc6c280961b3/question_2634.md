# Q2634: Account pre-funded before deployment - oversized deposit

## Question
Can an unprivileged attacker send NEAR to the derived address before the factory's create batch runs, changing the `env::account_balance()` that `new` turns into `lockup_amount`, attaching a very large deposit that the failure path must refund, breaking the invariant that `lockup_amount` equals the NEAR the grant funded, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup-factory/src/lib.rs` - `LockupFactory::create / on_lockup_create`
- Entrypoint: `create(...)` - `#[payable]`, callable by ANY account with `MIN_ATTACHED_BALANCE`
- Attacker controls: `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` and the attached deposit
- Exploit idea: Send NEAR to the derived address before the factory's create batch runs, changing the `env::account_balance()` that `new` turns into `lockup_amount`, attaching a very large deposit that the failure path must refund.
- Invariant to test: `lockup_amount` equals the NEAR the grant funded.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim pre-funding then creation and inspect `lockup_amount`.
