# Q2278: Withdraw racing an in-flight restake - amount = balance - 1

## Question
Can an unprivileged attacker withdraw while a `Promise::stake` from an earlier action is unresolved, so the liquid balance the transfer needs is still locked, with `amount` one yoctoNEAR below the attacker's own recorded balance, breaking the invariant that the pool always holds enough unlocked NEAR to honour every matured `unstaked` balance, and leading to temporary freezing of user funds for at least four epochs?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw while a `Promise::stake` from an earlier action is unresolved, so the liquid balance the transfer needs is still locked, with `amount` one yoctoNEAR below the attacker's own recorded balance.
- Invariant to test: The pool always holds enough unlocked NEAR to honour every matured `unstaked` balance.
- Expected Immunefi impact: High - temporary freezing of user funds for at least four epochs.
- Fast validation: Sim the race and assert the transfer succeeds.
