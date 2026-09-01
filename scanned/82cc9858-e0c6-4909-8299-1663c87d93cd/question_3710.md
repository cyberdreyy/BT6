# Q3710: Deposit credited to a different predecessor - sender unregisters

## Question
Can an unprivileged attacker arrange for `env::predecessor_account_id()` at deposit time to differ from the account that actually funded it, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`, breaking the invariant that minted tokens go to the funder, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Arrange for `env::predecessor_account_id()` at deposit time to differ from the account that actually funded it, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`.
- Invariant to test: Minted tokens go to the funder.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a relayed deposit.
