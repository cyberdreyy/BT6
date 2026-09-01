# Q1732: Deposit to a row at the unlock boundary - no account row yet

## Question
Can an unprivileged attacker deposit into a row whose `unstaked_available_epoch_height` is in the future, mixing matured and unmatured unstaked NEAR in one field, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that matured and unmatured unstaked NEAR are never conflated in a single deadline, and leading to direct theft of delegator NEAR from the staking pool?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Deposit into a row whose `unstaked_available_epoch_height` is in the future, mixing matured and unmatured unstaked NEAR in one field, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: Matured and unmatured unstaked NEAR are never conflated in a single deadline.
- Expected Immunefi impact: Critical - direct theft of delegator NEAR from the staking pool.
- Fast validation: Sim mixed maturities and assert what is withdrawable.
