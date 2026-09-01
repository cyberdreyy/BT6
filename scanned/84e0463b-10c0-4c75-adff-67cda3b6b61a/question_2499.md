# Q2499: Registration slot reused without payment - racing an in-flight call

## Question
Can an unprivileged attacker unregister and re-register in a pattern where the second registration is not charged, while an `ft_transfer_call` for the same balance is still in flight, breaking the invariant that each live registration is paid for, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/legacy_storage.rs` - `impl_fungible_token_storage! - storage_deposit / storage_withdraw / storage_unregister / storage_minimum_balance`
- Entrypoint: the storage-management methods on wNEAR - any account
- Attacker controls: the deposit amounts, the `force` flag and the registration lifecycle
- Exploit idea: Unregister and re-register in a pattern where the second registration is not charged, while an `ft_transfer_call` for the same balance is still in flight.
- Invariant to test: Each live registration is paid for.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the cycle and track NEAR.
