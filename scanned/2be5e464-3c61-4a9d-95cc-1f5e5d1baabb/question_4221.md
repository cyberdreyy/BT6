# Q4221: Receiver_id validation bypass - balance seeded before init

## Question
Can an unprivileged attacker pass a `receiver_id` that `env::is_valid_account_id` accepts but that routes NEAR somewhere the schedule never intended, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that NEAR leaves only to the account the owner named, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Pass a `receiver_id` that `env::is_valid_account_id` accepts but that routes NEAR somewhere the schedule never intended, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: NEAR leaves only to the account the owner named.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test adversarial receiver strings.
