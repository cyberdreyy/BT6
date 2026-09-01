# Q3386: One-yocto guard on transfers - receiver unregisters

## Question
Can an unprivileged attacker reach a transfer path where the standard's one-yocto requirement is satisfied by a relayer rather than the owner, with a `receiver_id` contract that unregisters itself before `ft_resolve_transfer` runs, breaking the invariant that the one-yocto guard binds the balance owner, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Reach a transfer path where the standard's one-yocto requirement is satisfied by a relayer rather than the owner, with a `receiver_id` contract that unregisters itself before `ft_resolve_transfer` runs.
- Invariant to test: The one-yocto guard binds the balance owner.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a relayed transfer.
