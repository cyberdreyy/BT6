# Q3618: Deposit of exactly the bound mints zero - sender unregisters

## Question
Can an unprivileged attacker attach exactly `storage_balance_bounds().min` so registration consumes everything and a zero-value account row is created, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`, breaking the invariant that no account row exists without either tokens or a paid registration, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Attach exactly `storage_balance_bounds().min` so registration consumes everything and a zero-value account row is created, when the sender unregisters between `ft_transfer_call` and `ft_resolve_transfer`.
- Invariant to test: No account row exists without either tokens or a paid registration.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test at the exact bound.
