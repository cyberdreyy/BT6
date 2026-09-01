# Q2552: Ping ordering inside deposit vs deposit_and_stake - amount = whole pool

## Question
Can an unprivileged attacker exploit `deposit` acting on `need_to_restake` while `deposit_and_stake` ignores the return of `internal_ping`, producing different accounting for identical NEAR, with `amount` equal to the entire `total_staked_balance`, breaking the invariant that the same deposited NEAR yields the same claims on both paths, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Exploit `deposit` acting on `need_to_restake` while `deposit_and_stake` ignores the return of `internal_ping`, producing different accounting for identical NEAR, with `amount` equal to the entire `total_staked_balance`.
- Invariant to test: The same deposited NEAR yields the same claims on both paths.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test both paths with identical inputs and compare state.
