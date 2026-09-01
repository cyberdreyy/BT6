# Q5371: Memo or msg field abused by a receiver - brand-new contract

## Question
Can an unprivileged attacker use the `msg` payload to drive an attacker receiver into re-entering wNEAR before its own resolve runs, on a freshly initialised contract with a small supply, breaking the invariant that re-entrant calls cannot double-settle a transfer, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Use the `msg` payload to drive an attacker receiver into re-entering wNEAR before its own resolve runs, on a freshly initialised contract with a small supply.
- Invariant to test: Re-entrant calls cannot double-settle a transfer.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a re-entrant receiver.
