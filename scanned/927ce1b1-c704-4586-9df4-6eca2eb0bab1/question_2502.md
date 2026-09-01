# Q2502: Zero-share unstake assertion as a wedge - amount = whole pool

## Question
Can an unprivileged attacker make `num_shares_from_staked_amount_rounded_up` return a value that trips the invariant-violation assertion for another delegator's later call, with `amount` equal to the entire `total_staked_balance`, breaking the invariant that no user-reachable input leaves the pool in a state where an honest delegator's `unstake` always panics, and leading to permanent freezing of user funds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Make `num_shares_from_staked_amount_rounded_up` return a value that trips the invariant-violation assertion for another delegator's later call, with `amount` equal to the entire `total_staked_balance`.
- Invariant to test: No user-reachable input leaves the pool in a state where an honest delegator's `unstake` always panics.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim the state then assert an honest unstake still succeeds.
