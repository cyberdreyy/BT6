# Q4158: Transfer during a termination window - balance seeded before init

## Question
Can an unprivileged attacker move NEAR out while `vesting_information` is `Terminating` but before the foundation's withdrawal completes, so `assert_no_termination` is passed by ordering, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that no NEAR leaves the account while a termination is pending, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Move NEAR out while `vesting_information` is `Terminating` but before the foundation's withdrawal completes, so `assert_no_termination` is passed by ordering, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: No NEAR leaves the account while a termination is pending.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a termination then attempt the transfer.
