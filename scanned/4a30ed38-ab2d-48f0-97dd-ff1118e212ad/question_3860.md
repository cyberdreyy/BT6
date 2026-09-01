# Q3860: Deposit sent to a pool that keeps it - same receipt as poll flip

## Question
Can an unprivileged attacker deposit into a selected contract that accepts the NEAR and reports whatever balance the attacker wants back, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`, breaking the invariant that `deposit_amount` equals NEAR the selected pool really owes the lockup, and leading to user funds routed into an attacker-parameterised contract the protocol treats as trusted?

## Target
- File/function: `lockup/src/owner.rs` - `select_staking_pool / deposit_to_staking_pool / deposit_and_stake / withdraw_from_staking_pool / refresh_staking_pool_balance`
- Entrypoint: the lockup's staking methods - the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: which pool is selected, the amounts, and the ordering of the resulting promises
- Exploit idea: Deposit into a selected contract that accepts the NEAR and reports whatever balance the attacker wants back, in the same receipt where `on_get_result_from_transfer_poll` flipped state to `TransfersEnabled`.
- Invariant to test: `deposit_amount` equals NEAR the selected pool really owes the lockup.
- Expected Immunefi impact: Critical - user funds routed into an attacker-parameterised contract the protocol treats as trusted.
- Fast validation: Sim a hostile pool contract and reconcile.
