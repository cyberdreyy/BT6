# Q2249: Storage refund exceeds the deposit - whole supply

## Question
Can an unprivileged attacker withdraw more storage NEAR than was deposited by cycling registration, moving an amount equal to the entire `ft.total_supply`, breaking the invariant that storage NEAR out never exceeds storage NEAR in, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Withdraw more storage NEAR than was deposited by cycling registration, moving an amount equal to the entire `ft.total_supply`.
- Invariant to test: Storage NEAR out never exceeds storage NEAR in.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Loop register/unregister and sum.
