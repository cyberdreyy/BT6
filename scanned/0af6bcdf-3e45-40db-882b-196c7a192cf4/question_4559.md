# Q4559: Promise result type confusion - no vesting schedule

## Question
Can an unprivileged attacker return a value of an unexpected shape from the named contract so the `#[callback]` deserialisation takes a path the contract does not expect, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch, breaking the invariant that a malformed callback value can never be interpreted as a valid balance, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner_callbacks.rs` - `on_whitelist_is_whitelisted / on_staking_pool_* / on_get_account_total_balance / on_get_result_from_transfer_poll`
- Entrypoint: callbacks reached through the owner methods; their inputs come from contracts the caller named
- Attacker controls: the contract that supplies the callback value and the success or failure of the promise
- Exploit idea: Return a value of an unexpected shape from the named contract so the `#[callback]` deserialisation takes a path the contract does not expect, on a lockup created with `vesting_schedule = None`, where `VestingInformation::None` short-circuits the unvested branch.
- Invariant to test: A malformed callback value can never be interpreted as a valid balance.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim a contract returning malformed JSON.
