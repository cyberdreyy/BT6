# Q5200: Two staking operations interleaved in one block - lockup_duration = 0

## Question
Can an unprivileged attacker start a second staking operation in the window where the first has set `Busy` but the receipt ordering lets the assertion pass, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`, breaking the invariant that only one staking operation is ever in flight, and leading to protocol accounting value diverges from reality and another party settles on it?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Start a second staking operation in the window where the first has set `Busy` but the receipt ordering lets the assertion pass, on a lockup created with `lockup_duration = 0` and no `lockup_timestamp`.
- Invariant to test: Only one staking operation is ever in flight.
- Expected Immunefi impact: High - protocol accounting value diverges from reality and another party settles on it.
- Fast validation: Sim two same-block staking calls.
