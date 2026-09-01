# Q0324: Storage deposit credited as token balance - exactly min deposit

## Question
Can an unprivileged attacker reach a path where storage NEAR is folded into the token balance or vice versa, attaching exactly `storage_balance_bounds().min`, breaking the invariant that storage NEAR and token backing are never conflated, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Reach a path where storage NEAR is folded into the token balance or vice versa, attaching exactly `storage_balance_bounds().min`.
- Invariant to test: Storage NEAR and token backing are never conflated.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test both paths and reconcile.
