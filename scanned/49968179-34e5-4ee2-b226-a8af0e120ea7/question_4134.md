# Q4134: Owner fee minted at the post-reward price - first delegator

## Question
Can an unprivileged attacker time the ping so `num_shares_from_staked_amount_rounded_down(owners_fee)` is computed after `total_staked_balance` already absorbed the whole reward, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that the owner's fee shares are worth exactly `owners_fee`, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Time the ping so `num_shares_from_staked_amount_rounded_down(owners_fee)` is computed after `total_staked_balance` already absorbed the whole reward, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: The owner's fee shares are worth exactly `owners_fee`.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Unit test the fee minting arithmetic against the fraction.
