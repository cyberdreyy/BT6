# Q1873: Self-transfer accounting - forced unregister with balance

## Question
Can an unprivileged attacker transfer to oneself so the debit and credit paths both run on one row, while the account still holds a non-zero token balance and calls `storage_unregister { force: true }`, breaking the invariant that a self transfer is a no-op on the total balance, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Transfer to oneself so the debit and credit paths both run on one row, while the account still holds a non-zero token balance and calls `storage_unregister { force: true }`.
- Invariant to test: A self transfer is a no-op on the total balance.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test a self transfer.
