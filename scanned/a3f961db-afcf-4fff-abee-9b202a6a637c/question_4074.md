# Q4074: Key added straight after the last vesting nanosecond - same receipt as poll flip

## Question
Can an unprivileged attacker add a full access key in the same block the schedule completes but before a pending termination or staking callback settles, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`, breaking the invariant that the key gate accounts for all pending obligations, not just the time-based schedule, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Add a full access key in the same block the schedule completes but before a pending termination or staking callback settles, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`.
- Invariant to test: The key gate accounts for all pending obligations, not just the time-based schedule.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim the boundary with a pending obligation.
