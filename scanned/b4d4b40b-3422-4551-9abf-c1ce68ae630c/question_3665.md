# Q3665: Deposit racing a transfer_call refund - sender unregisters

## Question
Can an unprivileged attacker deposit and withdraw around an unresolved transfer_call so the refund credits a balance already withdrawn, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`, breaking the invariant that each unit of value settles once, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Deposit and withdraw around an unresolved transfer_call so the refund credits a balance already withdrawn, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`.
- Invariant to test: Each unit of value settles once.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the interleaving.
