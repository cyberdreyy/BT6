# Q0949: Unregister during an in-flight transfer - re-registration

## Question
Can an unprivileged attacker unregister while a transfer involving the account is unresolved, after a forced `storage_unregister` removed the account and it registers again, breaking the invariant that an account cannot deregister while it has unsettled obligations, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Unregister while a transfer involving the account is unresolved, after a forced `storage_unregister` removed the account and it registers again.
- Invariant to test: An account cannot deregister while it has unsettled obligations.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the race.
