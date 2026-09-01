# Q0060: Busy status wedged permanently - before unlock

## Question
Can an unprivileged attacker leave `TransactionStatus::Busy` set by making the callback that clears it fail or never run, so every staking method and `unselect_staking_pool` panics forever, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`, breaking the invariant that any `Busy` status is eventually cleared by a callback that always runs, and leading to permanent freezing of user funds?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Leave `TransactionStatus::Busy` set by making the callback that clears it fail or never run, so every staking method and `unselect_staking_pool` panics forever, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`.
- Invariant to test: Any `Busy` status is eventually cleared by a callback that always runs.
- Expected Immunefi impact: Critical - permanent freezing of user funds.
- Fast validation: Sim a callback failure and assert the lockup is still usable.
