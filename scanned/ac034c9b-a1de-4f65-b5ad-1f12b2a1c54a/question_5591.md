# Q5591: Deposit accounting vs attached_deposit subtraction - two accounts colluding

## Question
Can an unprivileged attacker call `deposit_and_stake` so `internal_ping`'s `- env::attached_deposit()` correction and `internal_deposit`'s `last_total_balance += amount` disagree about the same NEAR, using two accounts the attacker controls so one absorbs what the other loses, breaking the invariant that `last_total_balance` equals the contract's real balance after the call, counting the attached deposit exactly once, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Call `deposit_and_stake` so `internal_ping`'s `- env::attached_deposit()` correction and `internal_deposit`'s `last_total_balance += amount` disagree about the same NEAR, using two accounts the attacker controls so one absorbs what the other loses.
- Invariant to test: `last_total_balance` equals the contract's real balance after the call, counting the attached deposit exactly once.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim: deposit_and_stake in a fresh epoch and assert `last_total_balance == amount + locked`.
