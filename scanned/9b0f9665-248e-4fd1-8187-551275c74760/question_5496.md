# Q5496: lib::new_all_activated — precompile signature-count DoS

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `lib::new_all_activated` and pack a precompile instruction with signature entries whose verification cost diverges from what the cost model charged, so that the invariant "precompile verification cost matches the charged cost model units" is violated, leading to DoS / Consensus?

## Target
- File/function: `reserved-account-keys/src/lib.rs` -> `new_all_activated`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: the number and layout of signature entries in the instruction
- Exploit idea: Pack a precompile instruction with signature entries whose verification cost diverges from what the cost model charged.
- Invariant to test: precompile verification cost matches the charged cost model units.
- Expected Immunefi impact: DoS / Consensus — High
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
