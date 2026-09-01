# Q4381: Resolve after the sender re-registers - racing an in-flight call

## Question
Can an unprivileged attacker unregister and re-register the sender across the callback so the refund lands in a fresh row with different assumptions, while an `ft_transfer_call` for the same balance is still in flight, breaking the invariant that refunds are unaffected by registration churn, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Unregister and re-register the sender across the callback so the refund lands in a fresh row with different assumptions, while an `ft_transfer_call` for the same balance is still in flight.
- Invariant to test: Refunds are unaffected by registration churn.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the churn.
