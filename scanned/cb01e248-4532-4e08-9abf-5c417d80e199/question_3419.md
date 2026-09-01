# Q3419: Deposit while the pool is bricked - right after pool creation

## Question
Can an unprivileged attacker deposit into a pool whose `internal_ping` assertion already fails, so NEAR is accepted into an account that can never withdraw, on a pool created moments earlier through the public `create_staking_pool`, breaking the invariant that any accepted deposit remains withdrawable, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Deposit into a pool whose `internal_ping` assertion already fails, so NEAR is accepted into an account that can never withdraw, on a pool created moments earlier through the public `create_staking_pool`.
- Invariant to test: Any accepted deposit remains withdrawable.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Reach the bricked state then deposit and try to withdraw.
