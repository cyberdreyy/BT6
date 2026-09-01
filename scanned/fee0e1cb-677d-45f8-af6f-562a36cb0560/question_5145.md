# Q5145: Transfers enabled by a poll the owner chose - lockup_duration = 0

## Question
Can an unprivileged attacker enable transfers through a `transfer_poll_account_id` the attacker controls, then transfer everything the lockup holds, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that transfers only enable on the genuine poll's 2/3 outcome, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `LockupContract::transfer / add_full_access_key`
- Entrypoint: `transfer(amount, receiver_id)` and `add_full_access_key(new_public_key)` - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the amount, the receiver, the timing, and every state the getters read
- Exploit idea: Enable transfers through a `transfer_poll_account_id` the attacker controls, then transfer everything the lockup holds, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: Transfers only enable on the genuine poll's 2/3 outcome.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a hostile poll contract returning a timestamp.
