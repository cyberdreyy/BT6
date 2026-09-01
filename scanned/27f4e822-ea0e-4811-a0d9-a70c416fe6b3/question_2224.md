# Q2224: Forced unregister burns a live balance - whole supply

## Question
Can an unprivileged attacker call `storage_unregister { force: true }` while holding tokens so the balance disappears while its backing NEAR stays in the contract, moving an amount equal to the entire `ft.total_supply`, breaking the invariant that supply decreases only when the matching NEAR leaves to the holder, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Call `storage_unregister { force: true }` while holding tokens so the balance disappears while its backing NEAR stays in the contract, moving an amount equal to the entire `ft.total_supply`.
- Invariant to test: Supply decreases only when the matching NEAR leaves to the holder.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test the forced path and reconcile.
