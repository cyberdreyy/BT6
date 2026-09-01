# Q4851: Price inflation via bare donation - attacker holds most shares

## Question
Can an unprivileged attacker send NEAR straight to the pool account, let `internal_ping` treat it as a reward, and capture it through shares bought around that ping, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker, breaking the invariant that `total_reward` reflects only NEAR the staking mechanism paid this account, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Send NEAR straight to the pool account, let `internal_ping` treat it as a reward, and capture it through shares bought around that ping, while holding the majority of `total_stake_shares`, so rounding accrues mostly to the attacker.
- Invariant to test: `total_reward` reflects only NEAR the staking mechanism paid this account.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim a bare transfer to the pool then ping, asserting no delegator can extract more than they contributed.
