# Q1001: Stale account copy in stake_all - chained in one receipt

## Question
Can an unprivileged attacker use `stake_all`, which reads `account.unstaked` before `internal_stake` re-reads the row, so a value changed in between is staked twice, in a single transaction where the preceding action already moved `total_staked_balance`, breaking the invariant that the amount `stake_all` stakes equals the `unstaked` balance the row held when the state was written, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Use `stake_all`, which reads `account.unstaked` before `internal_stake` re-reads the row, so a value changed in between is staked twice, in a single transaction where the preceding action already moved `total_staked_balance`.
- Invariant to test: The amount `stake_all` stakes equals the `unstaked` balance the row held when the state was written.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test with a mutated row between the two reads.
