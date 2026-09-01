# Q1607: Zero-value deposit creating a row - no account row yet

## Question
Can an unprivileged attacker attach zero and still create or touch a row, so the accounts map and the totals disagree, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that no row exists without a positive balance behind it, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Attach zero and still create or touch a row, so the accounts map and the totals disagree, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: No row exists without a positive balance behind it.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Unit test a zero deposit.
