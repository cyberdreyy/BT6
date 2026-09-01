# Q1657: Deposit and stake with an unstakeable amount - no account row yet

## Question
Can an unprivileged attacker deposit and stake an amount below the protocol's minimum stake so the stake action fails but the balance is already booked as staked, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`, breaking the invariant that booked staked balance equals stake the protocol accepted, and leading to protocol insolvency: recorded claims exceed the NEAR the contract actually holds?

## Target
- File/function: `staking-pool/src/internal.rs` - `StakingContract::internal_deposit / deposit / deposit_and_stake`
- Entrypoint: `deposit()` and `deposit_and_stake()` - payable, any account
- Attacker controls: the attached deposit, the account id it credits, and the surrounding epoch state
- Exploit idea: Deposit and stake an amount below the protocol's minimum stake so the stake action fails but the balance is already booked as staked, from an account with no row in `accounts`, so `internal_get_account` returns `Account::default()`.
- Invariant to test: Booked staked balance equals stake the protocol accepted.
- Expected Immunefi impact: Critical - protocol insolvency: recorded claims exceed the NEAR the contract actually holds.
- Fast validation: Sim a failing stake action after deposit_and_stake.
