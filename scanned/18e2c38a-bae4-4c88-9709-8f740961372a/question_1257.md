# Q1257: Zero-value deposit creating a row - amount = balance - 1

## Question
Can an unprivileged attacker attach zero and still create or touch a row, so the accounts map and the totals disagree, with `amount` one yoctoNEAR below the attacker's own recorded balance, breaking the invariant that no row exists without a positive balance behind it, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Attach zero and still create or touch a row, so the accounts map and the totals disagree, with `amount` one yoctoNEAR below the attacker's own recorded balance.
- Invariant to test: No row exists without a positive balance behind it.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test a zero deposit.
