# Q5622: Registration charge counted twice - relayed call

## Question
Can an unprivileged attacker deposit so the `storage_balance_bounds().min` subtraction happens on a path where the account was already registered, minting less than the NEAR received or crediting the difference nowhere, through a relayer contract that forwards the call, breaking the invariant that minted tokens plus retained registration NEAR equals the attached deposit, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Deposit so the `storage_balance_bounds().min` subtraction happens on a path where the account was already registered, minting less than the NEAR received or crediting the difference nowhere, through a relayer contract that forwards the call.
- Invariant to test: Minted tokens plus retained registration NEAR equals the attached deposit.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test first and repeat deposits and sum both sides.
