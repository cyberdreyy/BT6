# Q0761: Callback runs with the pool already unselected - release_duration = 0

## Question
Can an unprivileged attacker have `staking_information` become `None` before a callback that calls `.unwrap()` on it, panicking mid-flight and stranding the operation, on a lockup created with `release_duration = Some(0)`, breaking the invariant that callbacks never panic on state the owner can change while they are in flight, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Have `staking_information` become `None` before a callback that calls `.unwrap()` on it, panicking mid-flight and stranding the operation, on a lockup created with `release_duration = Some(0)`.
- Invariant to test: Callbacks never panic on state the owner can change while they are in flight.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim unselect racing a callback.
