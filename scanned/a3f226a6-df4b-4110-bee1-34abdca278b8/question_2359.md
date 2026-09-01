# Q2359: Liquid balance inflated by a hostile pool report - private VestingHash

## Question
Can an unprivileged attacker have the selected pool report an inflated total so `get_owners_balance` unlocks locked NEAR for transfer, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`, breaking the invariant that a pool's self-reported number cannot raise the transferable balance above real assets minus locked, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Have the selected pool report an inflated total so `get_owners_balance` unlocks locked NEAR for transfer, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`.
- Invariant to test: A pool's self-reported number cannot raise the transferable balance above real assets minus locked.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a hostile pool total then transfer.
