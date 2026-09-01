# Q3648: Many pings in one block - no account row yet

## Question
Can an unprivileged attacker call `ping` repeatedly inside one block so `last_epoch_height` short-circuits some paths but not the balance bookkeeping, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that repeated pings in one block are idempotent, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Call `ping` repeatedly inside one block so `last_epoch_height` short-circuits some paths but not the balance bookkeeping, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: Repeated pings in one block are idempotent.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim repeated pings and diff the state.
