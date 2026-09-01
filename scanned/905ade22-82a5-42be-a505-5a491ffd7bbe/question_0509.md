# Q0509: Dust transfers below the rounding step - exact unlock block

## Question
Can an unprivileged attacker repeat transfers of amounts smaller than the schedule's per-nanosecond release step, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that the total transferred by time T never exceeds the amount released by T, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Repeat transfers of amounts smaller than the schedule's per-nanosecond release step, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: The total transferred by time T never exceeds the amount released by T.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Loop dust transfers in sim and compare the total.
