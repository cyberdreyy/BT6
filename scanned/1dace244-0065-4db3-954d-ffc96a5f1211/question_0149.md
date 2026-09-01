# Q0149: Unregister during an in-flight transfer - unregistered caller

## Question
Can an unprivileged attacker unregister while a transfer involving the account is unresolved, from an account absent from `ft.accounts`, breaking the invariant that an account cannot deregister while it has unsettled obligations, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Unregister while a transfer involving the account is unresolved, from an account absent from `ft.accounts`.
- Invariant to test: An account cannot deregister while it has unsettled obligations.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the race.
