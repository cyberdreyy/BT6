# Q3399: Saturating_sub hides a withdrawal mismatch - inflated deposit_amount

## Question
Can an unprivileged attacker make `on_staking_pool_withdraw` saturate `deposit_amount` to zero while more NEAR than that was actually returned, while `staking_information.deposit_amount` exceeds what the pool really owes this lockup, breaking the invariant that the field decreases by exactly the NEAR returned, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Make `on_staking_pool_withdraw` saturate `deposit_amount` to zero while more NEAR than that was actually returned, while `staking_information.deposit_amount` exceeds what the pool really owes this lockup.
- Invariant to test: The field decreases by exactly the NEAR returned.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Unit test the saturating branch.
