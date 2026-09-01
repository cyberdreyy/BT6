# Q0924: Storage deposit credited as token balance - re-registration

## Question
Can an unprivileged attacker reach a path where storage NEAR is folded into the token balance or vice versa, after a forced `storage_unregister` removed the account and it registers again, breaking the invariant that storage NEAR and token backing are never conflated, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Reach a path where storage NEAR is folded into the token balance or vice versa, after a forced `storage_unregister` removed the account and it registers again.
- Invariant to test: Storage NEAR and token backing are never conflated.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test both paths and reconcile.
