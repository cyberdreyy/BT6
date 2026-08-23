# Q1190: lib::get_precompile — precompile signature-count DoS

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `lib::get_precompile` and pack a precompile instruction with signature entries whose verification cost diverges from what the cost model charged, so that the invariant "precompile verification cost matches the charged cost model units" is violated, leading to DoS / Consensus?

## Target
- File/function: `precompiles/src/lib.rs` -> `get_precompile`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: the number and layout of signature entries in the instruction
- Exploit idea: Pack a precompile instruction with signature entries whose verification cost diverges from what the cost model charged.
- Invariant to test: precompile verification cost matches the charged cost model units.
- Expected Immunefi impact: DoS / Consensus — High
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
