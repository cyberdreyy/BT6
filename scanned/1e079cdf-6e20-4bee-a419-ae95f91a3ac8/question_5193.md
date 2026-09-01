# Q5193: Shares minted worth more than charged - paused pool

## Question
Can an unprivileged attacker call `stake(amount)` so the shares minted by `num_shares_from_staked_amount_rounded_down` redeem, through `staked_amount_from_num_shares_rounded_up`, for more than the `charge_amount` taken from `account.unstaked`, while `paused == true`, so `internal_restake` returns early and nothing is re-staked, breaking the invariant that `staked_amount_from_num_shares_rounded_up(minted_shares) <= charge_amount` holds for every `internal_stake`, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Call `stake(amount)` so the shares minted by `num_shares_from_staked_amount_rounded_down` redeem, through `staked_amount_from_num_shares_rounded_up`, for more than the `charge_amount` taken from `account.unstaked`, while `paused == true`, so `internal_restake` returns early and nothing is re-staked.
- Invariant to test: `staked_amount_from_num_shares_rounded_up(minted_shares) <= charge_amount` holds for every `internal_stake`.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: `cargo test -p staking-pool`: `deposit_and_stake(x)` then `unstake_all` + `withdraw_all` in the same epoch and assert NEAR out <= x.
