# Q4032: Transfer of the storage reserve - same receipt as poll flip

## Question
Can an unprivileged attacker transfer an amount that leaves less than `MIN_BALANCE_FOR_STORAGE` so the contract can no longer pay for its own state, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`, breaking the invariant that the account always retains its storage reserve, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Transfer an amount that leaves less than `MIN_BALANCE_FOR_STORAGE` so the contract can no longer pay for its own state, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`.
- Invariant to test: The account always retains its storage reserve.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim a maximal transfer and check remaining balance.
