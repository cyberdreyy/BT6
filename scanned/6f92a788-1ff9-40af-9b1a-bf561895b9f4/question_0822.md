# Q0822: Withdraw the whole supply - one yocto short

## Question
Can an unprivileged attacker burn and withdraw an amount equal to the entire supply so the contract's remaining NEAR cannot cover registered storage, attaching one yoctoNEAR less than `storage_balance_bounds().min`, breaking the invariant that registered storage deposits remain covered after any withdrawal, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Burn and withdraw an amount equal to the entire supply so the contract's remaining NEAR cannot cover registered storage, attaching one yoctoNEAR less than `storage_balance_bounds().min`.
- Invariant to test: Registered storage deposits remain covered after any withdrawal.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a full withdrawal and check the remainder.
