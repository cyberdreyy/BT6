# Q1477: Repeat unstake before the restake settles - first receipt of an epoch

## Question
Can an unprivileged attacker issue a second `unstake` before the `Promise::stake` from the first has resolved, so both price from the same unsettled state, in the first receipt of a new epoch, before any other account triggers `internal_ping`, breaking the invariant that concurrent unstakes in one block cannot together exceed the account's shares, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Issue a second `unstake` before the `Promise::stake` from the first has resolved, so both price from the same unsettled state, in the first receipt of a new epoch, before any other account triggers `internal_ping`.
- Invariant to test: Concurrent unstakes in one block cannot together exceed the account's shares.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim two unstakes in one block and reconcile.
