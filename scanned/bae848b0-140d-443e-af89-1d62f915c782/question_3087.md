# Q3087: Status cleared before the effect settles - after a donation

## Question
Can an unprivileged attacker exploit `set_staking_pool_status(TransactionStatus::Idle)` running before the branch that updates `deposit_amount`, letting another call slip in, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`, breaking the invariant that the status returns to `Idle` only after all state for that operation is final, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Exploit `set_staking_pool_status(TransactionStatus::Idle)` running before the branch that updates `deposit_amount`, letting another call slip in, after an outside account sent extra NEAR to the lockup with a bare `Transfer`, inflating `env::account_balance()`.
- Invariant to test: The status returns to `Idle` only after all state for that operation is final.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a re-entrant call in the gap.
