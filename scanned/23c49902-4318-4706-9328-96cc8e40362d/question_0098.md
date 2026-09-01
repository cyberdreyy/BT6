# Q0098: Transfer_call to a panicking receiver - unregistered caller

## Question
Can an unprivileged attacker route through a receiver that panics so the full amount is refunded while side effects elsewhere already assumed settlement, from an account absent from `ft.accounts`, breaking the invariant that a panicking receiver leaves balances exactly as before, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Route through a receiver that panics so the full amount is refunded while side effects elsewhere already assumed settlement, from an account absent from `ft.accounts`.
- Invariant to test: A panicking receiver leaves balances exactly as before.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a panicking receiver and diff balances.
