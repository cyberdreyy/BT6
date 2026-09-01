# Q4841: Total balance overwritten from a hostile report - just after unselect

## Question
Can an unprivileged attacker make `on_get_account_total_balance` assign an attacker-chosen `total_balance` straight into `deposit_amount`, with no bound against what was ever deposited, in the receipt right after `unselect_staking_pool` cleared the staking information, breaking the invariant that `deposit_amount` is bounded by the NEAR the lockup actually sent to the pool plus real rewards, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Make `on_get_account_total_balance` assign an attacker-chosen `total_balance` straight into `deposit_amount`, with no bound against what was ever deposited, in the receipt right after `unselect_staking_pool` cleared the staking information.
- Invariant to test: `deposit_amount` is bounded by the NEAR the lockup actually sent to the pool plus real rewards.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a pool returning a huge total and then attempt a transfer.
