# Q1122: Withdraw debits although the transfer fails - single yocto

## Question
Can an unprivileged attacker call `near_withdraw` with a predecessor state that makes `Promise::new(account_id).transfer(amount + 1)` fail, while `ft.internal_withdraw` already burned the balance with no resolver, attaching a single yoctoNEAR, breaking the invariant that `ft.total_supply` equals the NEAR the contract holds minus registered storage deposits, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Call `near_withdraw` with a predecessor state that makes `Promise::new(account_id).transfer(amount + 1)` fail, while `ft.internal_withdraw` already burned the balance with no resolver, attaching a single yoctoNEAR.
- Invariant to test: `ft.total_supply` equals the NEAR the contract holds minus registered storage deposits.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a failing transfer and compare supply against balance.
