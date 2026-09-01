# Q1524: Storage deposit credited as token balance - receiver panics

## Question
Can an unprivileged attacker reach a path where storage NEAR is folded into the token balance or vice versa, with a `receiver_id` contract the attacker deployed that panics inside `ft_on_transfer`, breaking the invariant that storage NEAR and token backing are never conflated, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Reach a path where storage NEAR is folded into the token balance or vice versa, with a `receiver_id` contract the attacker deployed that panics inside `ft_on_transfer`.
- Invariant to test: Storage NEAR and token backing are never conflated.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test both paths and reconcile.
