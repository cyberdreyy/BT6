# Q3650: Account map key collisions - with the reward fee at one

## Question
Can an unprivileged attacker register account ids that the `UnorderedMap` keying treats as one row, on a pool whose `reward_fee_fraction` is the full 1/1, breaking the invariant that distinct account ids always map to distinct rows, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `internal_get_account / internal_save_account / get_account`
- Entrypoint: any delegator method, plus the public view methods `get_account`, `get_accounts`, `get_number_of_accounts`
- Attacker controls: when its row exists, when it is deleted, and what an integrator reads from the views
- Exploit idea: Register account ids that the `UnorderedMap` keying treats as one row, on a pool whose `reward_fee_fraction` is the full 1/1.
- Invariant to test: Distinct account ids always map to distinct rows.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test with adversarial account id strings.
