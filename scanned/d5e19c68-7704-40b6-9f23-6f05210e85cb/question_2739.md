# Q2739: Unregister during an in-flight transfer - legacy bound

## Question
Can an unprivileged attacker unregister while a transfer involving the account is unresolved, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound, breaking the invariant that an account cannot deregister while it has unsettled obligations, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Unregister while a transfer involving the account is unresolved, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound.
- Invariant to test: An account cannot deregister while it has unsettled obligations.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the race.
