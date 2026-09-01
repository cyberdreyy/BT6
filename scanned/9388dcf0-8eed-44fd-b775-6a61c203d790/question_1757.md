# Q1757: Deposit credited twice in last_total_balance - row deleted by save

## Question
Can an unprivileged attacker call `deposit` so `internal_ping`'s subtraction of `env::attached_deposit()` and `internal_deposit`'s `last_total_balance += amount` overlap, from an account whose row `internal_save_account` deleted when both balances reached zero, breaking the invariant that `last_total_balance` counts each attached deposit exactly once, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Call `deposit` so `internal_ping`'s subtraction of `env::attached_deposit()` and `internal_deposit`'s `last_total_balance += amount` overlap, from an account whose row `internal_save_account` deleted when both balances reached zero.
- Invariant to test: `last_total_balance` counts each attached deposit exactly once.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Unit test a fresh-epoch deposit and assert the totals.
