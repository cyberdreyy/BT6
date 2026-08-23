# Q1045: mod::process_new_vote_state_from_lockouts — reserved-key gate bypass

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `mod::process_new_vote_state_from_lockouts` and reference a reserved/protected account key so a write or ownership check that relies on the reserved set is skipped, so that the invariant "reserved account keys are never writable or reassignable by user transactions" is violated, leading to Consensus/Safety Violation?

## Target
- File/function: `programs/vote/src/vote_state/mod.rs` -> `process_new_vote_state_from_lockouts`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: the account keys and writable flags in its transaction
- Exploit idea: Reference a reserved/protected account key so a write or ownership check that relies on the reserved set is skipped.
- Invariant to test: reserved account keys are never writable or reassignable by user transactions.
- Expected Immunefi impact: Consensus/Safety Violation — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
