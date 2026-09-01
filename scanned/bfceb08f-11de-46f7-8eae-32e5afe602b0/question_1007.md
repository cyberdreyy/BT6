# Q1007: Deposit while the pool is bricked - last block of an epoch

## Question
Can an unprivileged attacker deposit into a pool whose `internal_ping` assertion already fails, so NEAR is accepted into an account that can never withdraw, in the last block of an epoch so the state change lands before the reward is distributed, breaking the invariant that any accepted deposit remains withdrawable, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Deposit into a pool whose `internal_ping` assertion already fails, so NEAR is accepted into an account that can never withdraw, in the last block of an epoch so the state change lands before the reward is distributed.
- Invariant to test: Any accepted deposit remains withdrawable.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Reach the bricked state then deposit and try to withdraw.
