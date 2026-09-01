# Q0998: Balance clamped to the receiver's holdings - one yocto short

## Question
Can an unprivileged attacker make the resolver's clamp against the receiver's current balance discard tokens that should have been refunded, attaching one yoctoNEAR less than `storage_balance_bounds().min`, breaking the invariant that clamping never destroys value silently, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Make the resolver's clamp against the receiver's current balance discard tokens that should have been refunded, attaching one yoctoNEAR less than `storage_balance_bounds().min`.
- Invariant to test: Clamping never destroys value silently.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a receiver that spends the received tokens.
