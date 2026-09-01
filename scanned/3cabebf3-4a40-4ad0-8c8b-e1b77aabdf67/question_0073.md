# Q0073: Receiver unregisters before resolve - unregistered caller

## Question
Can an unprivileged attacker have the receiver unregister itself so the resolver's read of its balance mis-clamps the refund, from an account absent from `ft.accounts`, breaking the invariant that the refund equals the unused amount the receiver reported, bounded by what it still holds, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Have the receiver unregister itself so the resolver's read of its balance mis-clamps the refund, from an account absent from `ft.accounts`.
- Invariant to test: The refund equals the unused amount the receiver reported, bounded by what it still holds.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim receiver unregistration.
