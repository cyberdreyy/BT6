# Q0752: Receive_amount above the shares burned - chained in one receipt

## Question
Can an unprivileged attacker unstake so `staked_amount_from_num_shares_rounded_up(num_shares)` credits more `unstaked` NEAR than the shares burned were worth at the rounded-down price, in a single transaction where the preceding action already moved `total_staked_balance`, breaking the invariant that `receive_amount <= staked_amount_from_num_shares_rounded_down(num_shares_burned)` plus at most one yocto of guarantee fund, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Unstake so `staked_amount_from_num_shares_rounded_up(num_shares)` credits more `unstaked` NEAR than the shares burned were worth at the rounded-down price, in a single transaction where the preceding action already moved `total_staked_balance`.
- Invariant to test: `receive_amount <= staked_amount_from_num_shares_rounded_down(num_shares_burned)` plus at most one yocto of guarantee fund.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Property test unstake amounts across many share prices asserting the bound.
