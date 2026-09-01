# Q4701: Ping assertion turned into a permanent wedge - after a bare donation

## Question
Can an unprivileged attacker engineer `env::account_locked_balance() + env::account_balance() - env::attached_deposit() < last_total_balance` so the assertion in `internal_ping` aborts every state-changing method, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method, breaking the invariant that no unprivileged action can make that assertion unsatisfiable, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_ping / ping / on_stake_action`
- Entrypoint: `ping()` - callable by anyone; also reached from `deposit`, `stake`, `unstake`, `withdraw`
- Attacker controls: when the ping happens, how much NEAR is sitting on the account, and whether a stake action is in flight
- Exploit idea: Engineer `env::account_locked_balance() + env::account_balance() - env::attached_deposit() < last_total_balance` so the assertion in `internal_ping` aborts every state-changing method, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method.
- Invariant to test: No unprivileged action can make that assertion unsatisfiable.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Reach the state in sim then assert `ping` still succeeds.
