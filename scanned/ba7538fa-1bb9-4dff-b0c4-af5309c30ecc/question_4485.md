# Q4485: Withdraw the whole supply - legacy bound

## Question
Can an unprivileged attacker burn and withdraw an amount equal to the entire supply so the contract's remaining NEAR cannot cover registered storage, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound, breaking the invariant that registered storage deposits remain covered after any withdrawal, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Burn and withdraw an amount equal to the entire supply so the contract's remaining NEAR cannot cover registered storage, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound.
- Invariant to test: Registered storage deposits remain covered after any withdrawal.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a full withdrawal and check the remainder.
