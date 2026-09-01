# Q0536: Callback gas exhausted after a state write - exact unlock block

## Question
Can an unprivileged attacker starve a callback so it aborts after writing part of its state, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that callback state updates are all-or-nothing, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Starve a callback so it aborts after writing part of its state, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: Callback state updates are all-or-nothing.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim partial gas and inspect state.
