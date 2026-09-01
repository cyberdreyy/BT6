# Q3528: Storage_withdraw draining backing NEAR - many small accounts

## Question
Can an unprivileged attacker withdraw storage NEAR in a way that dips into the NEAR backing outstanding tokens, spread across many small registered accounts the attacker controls, breaking the invariant that backing NEAR is never withdrawable as storage, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Withdraw storage NEAR in a way that dips into the NEAR backing outstanding tokens, spread across many small registered accounts the attacker controls.
- Invariant to test: Backing NEAR is never withdrawable as storage.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a maximal storage withdrawal and reconcile.
