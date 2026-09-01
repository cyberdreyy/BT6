# Q2742: Unstake more than the account's shares can cover - amount = balance - 1

## Question
Can an unprivileged attacker request an `amount` whose rounded-up share count exceeds `account.stake_shares` only after the price moves inside the same receipt, with `amount` one yoctoNEAR below the attacker's own recorded balance, breaking the invariant that `num_shares <= account.stake_shares` is evaluated against the same price used to pay out, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::inner_unstake`
- Entrypoint: `unstake(amount)` / `unstake_all()` - any delegator
- Attacker controls: the unstaked amount, timing relative to `internal_ping`, and the number of repetitions
- Exploit idea: Request an `amount` whose rounded-up share count exceeds `account.stake_shares` only after the price moves inside the same receipt, with `amount` one yoctoNEAR below the attacker's own recorded balance.
- Invariant to test: `num_shares <= account.stake_shares` is evaluated against the same price used to pay out.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test at a price boundary where the two evaluations disagree.
