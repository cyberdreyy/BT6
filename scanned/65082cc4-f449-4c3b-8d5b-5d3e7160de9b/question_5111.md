# Q5111: Deposit credited although the promise failed - lockup_duration = 0

## Question
Can an unprivileged attacker find a path where `is_promise_success()` is false yet `deposit_amount` or the status still moves, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that state changes only on the branch matching the promise outcome, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Find a path where `is_promise_success()` is false yet `deposit_amount` or the status still moves, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: State changes only on the branch matching the promise outcome.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim failing promises for every callback and diff the state.
