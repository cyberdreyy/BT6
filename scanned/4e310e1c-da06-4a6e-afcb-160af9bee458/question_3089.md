# Q3089: Poll account id chosen by the creator - at the storage floor

## Question
Can an unprivileged attacker initialise with a `transfer_poll_account_id` under the attacker's control, so transfers can be enabled at will later, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero, breaking the invariant that the poll account is the canonical transfer poll, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Initialise with a `transfer_poll_account_id` under the attacker's control, so transfers can be enabled at will later, when `env::account_balance()` is within `MIN_BALANCE_FOR_STORAGE` of zero so `get_account_balance` saturates to zero.
- Invariant to test: The poll account is the canonical transfer poll.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a hostile poll id and enable transfers.
