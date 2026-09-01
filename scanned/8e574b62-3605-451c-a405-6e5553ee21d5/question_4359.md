# Q4359: Supply survives a failed refund - racing an in-flight call

## Question
Can an unprivileged attacker make a failed transfer's NEAR return to the contract while the supply stays burned, silently over-collateralising then mis-crediting, while an `ft_transfer_call` for the same balance is still in flight, breaking the invariant that supply and backing move together, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Make a failed transfer's NEAR return to the contract while the supply stays burned, silently over-collateralising then mis-crediting, while an `ft_transfer_call` for the same balance is still in flight.
- Invariant to test: Supply and backing move together.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim the refund and reconcile.
