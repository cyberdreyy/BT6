# Q1911: Status cleared before the effect settles - after vesting end

## Question
Can an unprivileged attacker exploit `set_staking_pool_status(TransactionStatus::Idle)` running before the branch that updates `deposit_amount`, letting another call slip in, after `end_timestamp`, when `get_unvested_amount` returns zero while release time remains, breaking the invariant that the status returns to `Idle` only after all state for that operation is final, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Exploit `set_staking_pool_status(TransactionStatus::Idle)` running before the branch that updates `deposit_amount`, letting another call slip in, after `end_timestamp`, when `get_unvested_amount` returns zero while release time remains.
- Invariant to test: The status returns to `Idle` only after all state for that operation is final.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a re-entrant call in the gap.
