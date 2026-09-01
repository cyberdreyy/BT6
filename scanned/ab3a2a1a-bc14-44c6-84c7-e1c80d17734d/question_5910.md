# Q5910: Stake amount recorded but stake action fails - with the reward fee at zero

## Question
Can an unprivileged attacker stake an amount that makes the follow-up `Promise::stake` fail the protocol's minimum-stake check, so `on_stake_action` unstakes to zero while `total_staked_balance` keeps the credit, on a pool whose `reward_fee_fraction` numerator is zero, breaking the invariant that after `on_stake_action` resolves, `total_staked_balance` equals what the pool actually has staked or has explicitly unstaked, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Stake an amount that makes the follow-up `Promise::stake` fail the protocol's minimum-stake check, so `on_stake_action` unstakes to zero while `total_staked_balance` keeps the credit, on a pool whose `reward_fee_fraction` numerator is zero.
- Invariant to test: After `on_stake_action` resolves, `total_staked_balance` equals what the pool actually has staked or has explicitly unstaked.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Force a failing stake action in sim and assert the two values reconcile.
