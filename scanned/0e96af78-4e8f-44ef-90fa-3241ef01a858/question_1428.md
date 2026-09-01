# Q1428: Withdraw of a balance credited by a failed unstake - straight after a reward

## Question
Can an unprivileged attacker withdraw `unstaked` NEAR credited by an unstake whose stake action later failed and was reverted by `on_stake_action`, immediately after a large epoch reward was folded into `total_staked_balance` but before other delegators act, breaking the invariant that credited `unstaked` corresponds to shares that were really burned and stake that was really released, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Withdraw `unstaked` NEAR credited by an unstake whose stake action later failed and was reverted by `on_stake_action`, immediately after a large epoch reward was folded into `total_staked_balance` but before other delegators act.
- Invariant to test: Credited `unstaked` corresponds to shares that were really burned and stake that was really released.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim the failing stake action then the withdraw.
