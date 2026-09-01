# Q4506: Deposit below the registration bound - legacy bound

## Question
Can an unprivileged attacker attach less than the minimum so registration fails after part of the accounting already ran, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound, breaking the invariant that a rejected deposit leaves no state change and no retained NEAR, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Attach less than the minimum so registration fails after part of the accounting already ran, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound.
- Invariant to test: A rejected deposit leaves no state change and no retained NEAR.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test just below the bound.
