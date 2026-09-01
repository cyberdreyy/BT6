# Q0797: The extra yocto refund double counted - one yocto short

## Question
Can an unprivileged attacker exploit `transfer(amount + 1)` returning the attached yocto so repeated withdraw calls leak more NEAR than was burned, attaching one yoctoNEAR less than `storage_balance_bounds().min`, breaking the invariant that NEAR leaving equals tokens burned plus the single attached yocto, and leading to unbacked wNEAR minted / theft of the NEAR backing the token?

## Target
- File/function: `w-near/src/w_near.rs` - `Contract::near_deposit / near_withdraw`
- Entrypoint: `near_deposit()` and `near_withdraw(amount)` - payable, any account
- Attacker controls: the attached deposit, the withdrawal amount, its registration state and the receiving account
- Exploit idea: Exploit `transfer(amount + 1)` returning the attached yocto so repeated withdraw calls leak more NEAR than was burned, attaching one yoctoNEAR less than `storage_balance_bounds().min`.
- Invariant to test: NEAR leaving equals tokens burned plus the single attached yocto.
- Expected Immunefi impact: Critical - unbacked wNEAR minted / theft of the NEAR backing the token.
- Fast validation: Loop withdrawals in sim and reconcile.
