# Q4284: Transfer of the storage reserve - balance seeded before init

## Question
Can an unprivileged attacker transfer an amount that leaves less than `MIN_BALANCE_FOR_STORAGE` so the contract can no longer pay for its own state, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that the account always retains its storage reserve, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Transfer an amount that leaves less than `MIN_BALANCE_FOR_STORAGE` so the contract can no longer pay for its own state, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: The account always retains its storage reserve.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim a maximal transfer and check remaining balance.
