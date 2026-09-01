# Q4265: Owner withdraw callback chain re-entered - balance seeded before init

## Question
Can an unprivileged attacker re-enter `on_get_account_unstaked_balance_to_withdraw_by_owner` so it schedules two withdrawals for one balance, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced, breaking the invariant that one unstaked balance results in exactly one withdrawal, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Re-enter `on_get_account_unstaked_balance_to_withdraw_by_owner` so it schedules two withdrawals for one balance, on a lockup whose account already held NEAR when `new` ran, so `lockup_amount` came from a balance an outsider influenced.
- Invariant to test: One unstaked balance results in exactly one withdrawal.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim the re-entrancy and count transfers.
