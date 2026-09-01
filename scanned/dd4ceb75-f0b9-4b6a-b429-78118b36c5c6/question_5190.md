# Q5190: Deposit into a contract with no state - large holder

## Question
Can an unprivileged attacker call `near_deposit` before `new` has run so the token state is absent or default, as a holder of a large fraction of the total supply, breaking the invariant that no deposit is accepted before initialisation, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Call `near_deposit` before `new` has run so the token state is absent or default, as a holder of a large fraction of the total supply.
- Invariant to test: No deposit is accepted before initialisation.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a pre-init deposit.
