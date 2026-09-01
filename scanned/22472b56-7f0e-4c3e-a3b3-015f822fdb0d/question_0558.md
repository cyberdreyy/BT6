# Q0558: Liquid balance min() defeated by donations - exact unlock block

## Question
Can an unprivileged attacker raise `env::account_balance()` with an outside transfer so `min(owners_balance, account_balance)` stops binding and locked NEAR becomes transferable, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`, breaking the invariant that donated NEAR increases the transferable balance by at most the donation, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/getters.rs` - `get_locked_amount / get_unvested_amount / get_owners_balance / get_liquid_owners_balance`
- Entrypoint: read by `transfer`, `add_full_access_key` and every staking method as the release gate
- Attacker controls: the schedule arguments chosen at creation, the account balance, and the block timestamp it is called at
- Exploit idea: Raise `env::account_balance()` with an outside transfer so `min(owners_balance, account_balance)` stops binding and locked NEAR becomes transferable, in the exact block where `max(transfers_timestamp + lockup_duration, lockup_timestamp) == env::block_timestamp()`.
- Invariant to test: Donated NEAR increases the transferable balance by at most the donation.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a donation then compare transferable before and after.
