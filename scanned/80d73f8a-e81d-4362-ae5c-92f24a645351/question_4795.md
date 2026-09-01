# Q4795: Transfer to an account that cannot be registered - dead receiver

## Question
Can an unprivileged attacker transfer to a receiver that has no storage registration so the credit lands nowhere while the debit stands, with a `receiver_id` account that does not exist, so the `Promise::transfer` fails after state was already written, breaking the invariant that every credited balance has a registered holder, or the transfer is rejected, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/lib.rs` - `impl_fungible_token_core! - ft_transfer / ft_transfer_call / ft_resolve_transfer`
- Entrypoint: the standard NEP-141 methods exposed on wNEAR - any holder, any receiver contract
- Attacker controls: the receiver contract's code and return value, the amounts, and the ordering of the resolve callback
- Exploit idea: Transfer to a receiver that has no storage registration so the credit lands nowhere while the debit stands, with a `receiver_id` account that does not exist, so the `Promise::transfer` fails after state was already written.
- Invariant to test: Every credited balance has a registered holder, or the transfer is rejected.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test transfers to unregistered receivers.
