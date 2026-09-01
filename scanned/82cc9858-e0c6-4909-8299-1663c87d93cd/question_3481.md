# Q3481: Resolver refunds more than it took - sender unregisters

## Question
Can an unprivileged attacker have the receiver return an unused amount larger than the transferred amount so `ft_resolve_transfer` re-credits more than was moved, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`, breaking the invariant that the refund never exceeds the amount transferred, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Have the receiver return an unused amount larger than the transferred amount so `ft_resolve_transfer` re-credits more than was moved, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`.
- Invariant to test: The refund never exceeds the amount transferred.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim an over-returning receiver and compare balances.
