# Q4129: Refund into an unregistered sender - racing an in-flight call

## Question
Can an unprivileged attacker unregister the sender between the transfer and the resolve so the refund is burned or credited to the wrong holder, while an `ft_transfer_call` for the same balance is still in flight, breaking the invariant that refunded tokens return to the original sender or the supply is reduced consistently, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Unregister the sender between the transfer and the resolve so the refund is burned or credited to the wrong holder, while an `ft_transfer_call` for the same balance is still in flight.
- Invariant to test: Refunded tokens return to the original sender or the supply is reduced consistently.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim sender unregistration mid-flight.
