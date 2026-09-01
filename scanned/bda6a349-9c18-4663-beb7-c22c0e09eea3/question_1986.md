# Q1986: Unstake_all callback misreports the released amount - after vesting end

## Question
Can an unprivileged attacker make the unstake-all callback record success while the pool released a different amount, after `end_timestamp`, when `get_unvested_amount` returns zero while release time remains, breaking the invariant that recorded released amount equals the pool's actual release, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Make the unstake-all callback record success while the pool released a different amount, after `end_timestamp`, when `get_unvested_amount` returns zero while release time remains.
- Invariant to test: Recorded released amount equals the pool's actual release.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a mismatching pool.
