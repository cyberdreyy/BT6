# Q2909: One-block reward capture - amount = balance - 1

## Question
Can an unprivileged attacker deposit and stake immediately before an epoch reward is distributed, then unstake right after `internal_ping`, capturing a full epoch's yield for one block of exposure, with `amount` one yoctoNEAR below the attacker's own recorded balance, breaking the invariant that reward credited to an account is proportional to the shares it held across the rewarded epoch, and leading to theft of staking rewards or fees owed to other parties?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_stake`
- Entrypoint: `stake(amount)` / `stake_all()` / `deposit_and_stake()` - any account, no role required
- Attacker controls: the staked amount, the call ordering inside a block, its own `unstaked` balance, and how many times it repeats
- Exploit idea: Deposit and stake immediately before an epoch reward is distributed, then unstake right after `internal_ping`, capturing a full epoch's yield for one block of exposure, with `amount` one yoctoNEAR below the attacker's own recorded balance.
- Invariant to test: Reward credited to an account is proportional to the shares it held across the rewarded epoch.
- Expected Immunefi impact: High - theft of staking rewards or fees owed to other parties.
- Fast validation: Sim two delegators, one staked all epoch and one for a block, and compare their reward shares.
