# Q1913: Owner set to a victim account - private VestingHash

## Question
Can an unprivileged attacker initialise a lockup naming a victim as owner with terms the victim never agreed to, at the address derived from their account id, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`, breaking the invariant that a lockup at the derived address of an account carries the terms that account's grantor chose, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup/src/lib.rs` - `LockupContract::new / VestingSchedule::assert_valid / VestingScheduleWithSalt::hash`
- Entrypoint: `new(...)` runs from the factory's deploy batch; its arguments come from whoever called the factory
- Attacker controls: every initialisation argument, plus the account balance at the moment `new` executes
- Exploit idea: Initialise a lockup naming a victim as owner with terms the victim never agreed to, at the address derived from their account id, on a lockup initialised with `VestingScheduleOrHash::VestingHash`, where `get_locked_amount` counts unvested as `U128(0)`.
- Invariant to test: A lockup at the derived address of an account carries the terms that account's grantor chose.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Create a lockup for a victim id and inspect the terms.
