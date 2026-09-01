# Q1772: Registration charge counted twice - forced unregister with balance

## Question
Can an unprivileged attacker deposit so the `storage_balance_bounds().min` subtraction happens on a path where the account was already registered, minting less than the NEAR received or crediting the difference nowhere, while the account still holds a non-zero token balance and calls `storage_unregister { force: true }`, breaking the invariant that minted tokens plus retained registration NEAR equals the attached deposit, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Deposit so the `storage_balance_bounds().min` subtraction happens on a path where the account was already registered, minting less than the NEAR received or crediting the difference nowhere, while the account still holds a non-zero token balance and calls `storage_unregister { force: true }`.
- Invariant to test: Minted tokens plus retained registration NEAR equals the attached deposit.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test first and repeat deposits and sum both sides.
