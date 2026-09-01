# Q3503: Registration charge skipped - sender unregisters

## Question
Can an unprivileged attacker reach `ft.internal_deposit` with the full attached amount for an account that still consumes a registration slot, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`, breaking the invariant that every registration is paid for exactly once, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Reach `ft.internal_deposit` with the full attached amount for an account that still consumes a registration slot, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`.
- Invariant to test: Every registration is paid for exactly once.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Unit test the registration branch.
