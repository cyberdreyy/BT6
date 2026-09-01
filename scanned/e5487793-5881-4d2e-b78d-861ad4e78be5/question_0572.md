# Q0572: Withdraw racing an in-flight transfer_call - exactly min deposit

## Question
Can an unprivileged attacker withdraw while an `ft_transfer_call` for the same balance is unresolved, so the refund path re-credits already-withdrawn value, attaching exactly `storage_balance_bounds().min`, breaking the invariant that one balance settles in exactly one of the two flows, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Withdraw while an `ft_transfer_call` for the same balance is unresolved, so the refund path re-credits already-withdrawn value, attaching exactly `storage_balance_bounds().min`.
- Invariant to test: One balance settles in exactly one of the two flows.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the interleaving and reconcile.
