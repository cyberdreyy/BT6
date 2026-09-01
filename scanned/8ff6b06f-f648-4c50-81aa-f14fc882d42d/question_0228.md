# Q0228: Withdraw of a balance credited by a failed unstake - 1-yocto amount

## Question
Can an unprivileged attacker withdraw `unstaked` NEAR credited by an unstake whose stake action later failed and was reverted by `on_stake_action`, with `amount = 1` yoctoNEAR so every U256 division truncates, breaking the invariant that credited `unstaked` corresponds to shares that were really burned and stake that was really released, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw `unstaked` NEAR credited by an unstake whose stake action later failed and was reverted by `on_stake_action`, with `amount = 1` yoctoNEAR so every U256 division truncates.
- Invariant to test: Credited `unstaked` corresponds to shares that were really burned and stake that was really released.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim the failing stake action then the withdraw.
