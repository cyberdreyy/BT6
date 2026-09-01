# Q4776: Withdraw the whole supply - dead receiver

## Question
Can an unprivileged attacker burn and withdraw an amount equal to the entire supply so the contract's remaining NEAR cannot cover registered storage, with a `receiver_id` account that does not exist, so the `Promise::transfer` fails after state was already written, breaking the invariant that registered storage deposits remain covered after any withdrawal, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Burn and withdraw an amount equal to the entire supply so the contract's remaining NEAR cannot cover registered storage, with a `receiver_id` account that does not exist, so the `Promise::transfer` fails after state was already written.
- Invariant to test: Registered storage deposits remain covered after any withdrawal.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a full withdrawal and check the remainder.
