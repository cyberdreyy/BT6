# Q4902: Rounding at the registration boundary - dead receiver

## Question
Can an unprivileged attacker deposit amounts near the bound repeatedly so the retained registration NEAR accumulates unaccounted, with a `receiver_id` account that does not exist, so the `Promise::transfer` fails after state was already written, breaking the invariant that retained NEAR equals the sum of registration fees for live registrations, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Deposit amounts near the bound repeatedly so the retained registration NEAR accumulates unaccounted, with a `receiver_id` account that does not exist, so the `Promise::transfer` fails after state was already written.
- Invariant to test: Retained NEAR equals the sum of registration fees for live registrations.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Loop deposits and reconcile.
