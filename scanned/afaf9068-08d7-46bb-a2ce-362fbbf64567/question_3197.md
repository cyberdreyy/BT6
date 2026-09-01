# Q3197: Charge/stake asymmetry drains the guarantee fund - u128::MAX

## Question
Can an unprivileged attacker exploit `internal_stake` adding `staked_amount_from_num_shares_rounded_up(num_shares)` to `total_staked_balance` while only deducting the rounded-down `charge_amount` from the account, with `amount = u128::MAX` so the U256 product dwarfs any real balance, breaking the invariant that `total_staked_balance` grows by at most the NEAR the account was actually charged, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Exploit `internal_stake` adding `staked_amount_from_num_shares_rounded_up(num_shares)` to `total_staked_balance` while only deducting the rounded-down `charge_amount` from the account, with `amount = u128::MAX` so the U256 product dwarfs any real balance.
- Invariant to test: `total_staked_balance` grows by at most the NEAR the account was actually charged.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Assert in a unit test that after N stakes `total_staked_balance <= sum(charged) + STAKE_SHARE_PRICE_GUARANTEE_FUND`.
