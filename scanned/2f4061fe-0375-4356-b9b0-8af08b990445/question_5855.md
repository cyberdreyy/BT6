# Q5855: Dust accumulation of the uncharged remainder - with the reward fee at zero

## Question
Can an unprivileged attacker repeatedly stake amounts where `amount - charge_amount` is left behind in `account.unstaked` yet full shares are minted, on a pool whose `reward_fee_fraction` numerator is zero, breaking the invariant that `account.unstaked + staked_amount_from_num_shares_rounded_down(account.stake_shares)` never grows without a deposit, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Repeatedly stake amounts where `amount - charge_amount` is left behind in `account.unstaked` yet full shares are minted, on a pool whose `reward_fee_fraction` numerator is zero.
- Invariant to test: `account.unstaked + staked_amount_from_num_shares_rounded_down(account.stake_shares)` never grows without a deposit.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Loop 10k dust stakes in `near-sdk-sim` and assert the account's total balance is non-increasing.
