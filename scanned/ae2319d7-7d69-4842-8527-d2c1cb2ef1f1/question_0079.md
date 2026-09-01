# Q0079: Attached_deposit subtracted twice - 1-yocto amount

## Question
Can an unprivileged attacker reach `internal_ping` on a payable path where `env::attached_deposit()` is also accounted by `internal_deposit`, so the same NEAR is netted twice, with `amount = 1` yoctoNEAR so every U256 division truncates, breaking the invariant that the attached deposit is counted exactly once in `last_total_balance`, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Reach `internal_ping` on a payable path where `env::attached_deposit()` is also accounted by `internal_deposit`, so the same NEAR is netted twice, with `amount = 1` yoctoNEAR so every U256 division truncates.
- Invariant to test: The attached deposit is counted exactly once in `last_total_balance`.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Unit test `deposit` and assert the resulting `last_total_balance`.
