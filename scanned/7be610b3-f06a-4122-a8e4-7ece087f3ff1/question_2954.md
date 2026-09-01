# Q2954: Resolve callback reachable directly - receiver over-refunds

## Question
Can an unprivileged attacker call the resolver in a way that satisfies its private guard without a genuine transfer behind it, with a `receiver_id` contract that returns an unused amount larger than the amount transferred, breaking the invariant that the resolver only runs as the callback of a transfer this contract scheduled, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Call the resolver in a way that satisfies its private guard without a genuine transfer behind it, with a `receiver_id` contract that returns an unused amount larger than the amount transferred.
- Invariant to test: The resolver only runs as the callback of a transfer this contract scheduled.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a crafted callback.
