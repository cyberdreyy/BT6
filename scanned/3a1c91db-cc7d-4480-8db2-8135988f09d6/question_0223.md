# Q0223: Deposit racing a transfer_call refund - unregistered caller

## Question
Can an unprivileged attacker deposit and withdraw around an unresolved transfer_call so the refund credits a balance already withdrawn, from an account absent from `ft.accounts`, breaking the invariant that each unit of value settles once, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Deposit and withdraw around an unresolved transfer_call so the refund credits a balance already withdrawn, from an account absent from `ft.accounts`.
- Invariant to test: Each unit of value settles once.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the interleaving.
