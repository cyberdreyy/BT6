# Q3877: Ping panic wedged by the drift - after a bare donation

## Question
Can an unprivileged attacker create the drift above so the next `internal_ping` sees `total_balance < last_total_balance` and its assertion aborts every ping-guarded method for all delegators, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method, breaking the invariant that no user action can put the pool in a state where `deposit`, `stake`, `unstake` and `withdraw` all panic, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_withdraw`
- Entrypoint: `withdraw(amount)` / `withdraw_all()` - any delegator
- Attacker controls: the withdrawal amount, the receiving account and its state, and the epoch in which it is called
- Exploit idea: Create the drift above so the next `internal_ping` sees `total_balance < last_total_balance` and its assertion aborts every ping-guarded method for all delegators, after first sending NEAR straight to the pool account with a bare `Transfer` outside any method.
- Invariant to test: No user action can put the pool in a state where `deposit`, `stake`, `unstake` and `withdraw` all panic.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: After the drift, assert an honest delegator's `deposit` still succeeds.
