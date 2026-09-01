# Q0237: Unvested recomputed after partial withdrawal - before unlock

## Question
Can an unprivileged attacker make the unvested figure recomputed after a partial termination withdrawal disagree with the remainder actually owed, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`, breaking the invariant that unvested owed minus withdrawn is monotone and exact, and leading to locked/unvested lockup tokens released to a party not entitled to them?

## Target
- File/function: `lockup/src/foundation_callbacks.rs` - `on_get_account_staked_balance_to_unstake / on_staking_pool_unstake_for_termination / on_withdraw_unvested_amount`
- Entrypoint: the termination state machine, driven by foundation calls but observable and race-able by the lockup owner - a position anyone can hold, since `LockupFactory::create` is payable and open to any caller
- Attacker controls: the account balance, the selected pool's answers, and the timing of owner actions around each step
- Exploit idea: Make the unvested figure recomputed after a partial termination withdrawal disagree with the remainder actually owed, before `lockup_timestamp` passes, while `get_locked_amount` still returns the full `lockup_amount`.
- Invariant to test: Unvested owed minus withdrawn is monotone and exact.
- Expected Immunefi impact: Critical - locked/unvested lockup tokens released to a party not entitled to them.
- Fast validation: Sim a partial withdrawal and reconcile.
