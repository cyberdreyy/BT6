# Q4590: One-yocto assertion bypassed by a proxy - legacy bound

## Question
Can an unprivileged attacker reach `near_withdraw` through a path where `assert_one_yocto()` is satisfied by a caller other than the balance owner, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound, breaking the invariant that the yocto assertion binds the balance owner, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Reach `near_withdraw` through a path where `assert_one_yocto()` is satisfied by a caller other than the balance owner, relying on the value `legacy_storage::storage_minimum_balance` reports rather than the current bound.
- Invariant to test: The yocto assertion binds the balance owner.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Sim a proxying contract.
