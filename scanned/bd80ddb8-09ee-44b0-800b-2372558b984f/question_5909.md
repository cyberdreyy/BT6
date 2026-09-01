# Q5909: Transfer of the whole supply to a contract that burns it - repeated in one block

## Question
Can an unprivileged attacker move the entire supply into a contract that cannot return it, stranding the NEAR backing, repeating the call several times inside one block, breaking the invariant that backing NEAR is always claimable by some holder, and leading to permanent freezing of user funds?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Move the entire supply into a contract that cannot return it, stranding the NEAR backing, repeating the call several times inside one block.
- Invariant to test: Backing NEAR is always claimable by some holder.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim the transfer and attempt recovery.
