# Q3439: Withdraw racing an in-flight restake - first delegator

## Question
Can an unprivileged attacker withdraw while a `Promise::stake` from an earlier action is unresolved, so the liquid balance the transfer needs is still locked, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that the pool always holds enough unlocked NEAR to honour every matured `unstaked` balance, and leading to temporary freezing of user funds for at least four epochs?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw while a `Promise::stake` from an earlier action is unresolved, so the liquid balance the transfer needs is still locked, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: The pool always holds enough unlocked NEAR to honour every matured `unstaked` balance.
- Expected Immunefi impact: High - temporary freezing of user funds for at least four epochs.
- Fast validation: Sim the race and assert the transfer succeeds.
