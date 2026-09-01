# Q0609: Transfer above the true liquid balance - release_duration = 0

## Question
Can an unprivileged attacker pass an `amount` that satisfies the `get_liquid_owners_balance() >= amount` assertion only because one of its inputs is stale or inflated, on a lockup created with `release_duration = Some(0)`, breaking the invariant that NEAR that leaves via `transfer` is at most the honestly computed liquid owners balance, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Pass an `amount` that satisfies the `get_liquid_owners_balance() >= amount` assertion only because one of its inputs is stale or inflated, on a lockup created with `release_duration = Some(0)`.
- Invariant to test: NEAR that leaves via `transfer` is at most the honestly computed liquid owners balance.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a transfer at the assertion boundary and reconcile against the schedule.
