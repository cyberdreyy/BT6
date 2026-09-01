# Q1906: Account map key collisions - no account row yet

## Question
Can an unprivileged attacker register account ids that the `UnorderedMap` keying treats as one row, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that distinct account ids always map to distinct rows, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Register account ids that the `UnorderedMap` keying treats as one row, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: Distinct account ids always map to distinct rows.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test with adversarial account id strings.
