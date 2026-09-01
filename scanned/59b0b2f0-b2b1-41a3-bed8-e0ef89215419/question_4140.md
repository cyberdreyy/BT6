# Q4140: Unvested amount frozen at a favourable moment - just after unselect

## Question
Can an unprivileged attacker influence the balance or schedule state read when `TerminationInformation::unvested_amount` is computed, so the frozen figure is lower than the schedule requires, in the receipt right after `unselect_staking_pool` cleared the staking information, breaking the invariant that the frozen unvested amount equals the schedule's unvested amount at termination time, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Influence the balance or schedule state read when `TerminationInformation::unvested_amount` is computed, so the frozen figure is lower than the schedule requires, in the receipt right after `unselect_staking_pool` cleared the staking information.
- Invariant to test: The frozen unvested amount equals the schedule's unvested amount at termination time.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Unit test termination at adversarial timestamps.
