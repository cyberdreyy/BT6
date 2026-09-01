# Q5082: Supply inflated by an unregistered credit - large holder

## Question
Can an unprivileged attacker credit an account that is not registered so the supply grows without a storage-covered holder, as a holder of a large fraction of the total supply, breaking the invariant that every token balance belongs to a registered account, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Credit an account that is not registered so the supply grows without a storage-covered holder, as a holder of a large fraction of the total supply.
- Invariant to test: Every token balance belongs to a registered account.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test crediting an unregistered account.
