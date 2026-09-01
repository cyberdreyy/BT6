# Q3585: Poll result accepted from any named contract - at the storage floor

## Question
Can an unprivileged attacker make `on_get_result_from_transfer_poll` accept a `PollResult` from a `transfer_poll_account_id` chosen at creation, permanently flipping transfers on, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero, breaking the invariant that transfers enable only on the canonical poll's real 2/3 result, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Make `on_get_result_from_transfer_poll` accept a `PollResult` from a `transfer_poll_account_id` chosen at creation, permanently flipping transfers on, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero.
- Invariant to test: Transfers enable only on the canonical poll's real 2/3 result.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a hostile poll and assert transfers stay disabled.
