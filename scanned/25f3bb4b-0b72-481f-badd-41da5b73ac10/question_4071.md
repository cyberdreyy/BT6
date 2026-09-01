# Q4071: Ping assertion turned into a permanent wedge - first delegator

## Question
Can an unprivileged attacker engineer `env::account_locked_balance() + env::account_balance() - env::attached_deposit() < last_total_balance` so the assertion in `internal_ping` aborts every state-changing method, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`, breaking the invariant that no unprivileged action can make that assertion unsatisfiable, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Engineer `env::account_locked_balance() + env::account_balance() - env::attached_deposit() < last_total_balance` so the assertion in `internal_ping` aborts every state-changing method, as the first delegator, while `total_stake_shares` is still only the seeded `STAKE_SHARE_PRICE_GUARANTEE_FUND`.
- Invariant to test: No unprivileged action can make that assertion unsatisfiable.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Reach the state in sim then assert `ping` still succeeds.
