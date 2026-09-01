# Q0274: Legacy bound disagrees with the live bound - exactly min deposit

## Question
Can an unprivileged attacker rely on `storage_minimum_balance` reporting a bound different from the one `near_deposit` enforces, so an integrator under-funds a registration, attaching exactly `storage_balance_bounds().min`, breaking the invariant that every reported bound equals the bound actually enforced, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Rely on `storage_minimum_balance` reporting a bound different from the one `near_deposit` enforces, so an integrator under-funds a registration, attaching exactly `storage_balance_bounds().min`.
- Invariant to test: Every reported bound equals the bound actually enforced.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Compare the two values in a unit test.
