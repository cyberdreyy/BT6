# Q3786: Ping through a non-payable method carrying a deposit - row deleted by save

## Question
Can an unprivileged attacker reach `internal_ping` from a method where `env::attached_deposit()` is non-zero but never credited to any account, from an account whose row `internal_save_account` deleted when both balances reached zero, breaking the invariant that NEAR attached to a call is either credited to the caller or rejected, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Reach `internal_ping` from a method where `env::attached_deposit()` is non-zero but never credited to any account, from an account whose row `internal_save_account` deleted when both balances reached zero.
- Invariant to test: NEAR attached to a call is either credited to the caller or rejected.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test each entrypoint with an attached deposit.
