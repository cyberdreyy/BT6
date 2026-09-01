# Q5002: Select after termination begins - just after unselect

## Question
Can an unprivileged attacker select or change the pool in the window where `assert_no_termination` has not yet observed the terminating state, in the receipt right after `unselect_staking_pool` cleared the staking information, breaking the invariant that no pool changes are possible once termination has begun, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Select or change the pool in the window where `assert_no_termination` has not yet observed the terminating state, in the receipt right after `unselect_staking_pool` cleared the staking information.
- Invariant to test: No pool changes are possible once termination has begun.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim the ordering and assert rejection.
