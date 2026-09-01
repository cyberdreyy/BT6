# Q5478: Registration charge skipped - many small accounts

## Question
Can an unprivileged attacker reach `ft.internal_deposit` with the full attached amount for an account that still consumes a registration slot, spread across many small registered accounts the attacker controls, breaking the invariant that every registration is paid for exactly once, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Reach `ft.internal_deposit` with the full attached amount for an account that still consumes a registration slot, spread across many small registered accounts the attacker controls.
- Invariant to test: Every registration is paid for exactly once.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test the registration branch.
