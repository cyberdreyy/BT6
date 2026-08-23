# Q5486: lib::get_precompiles — system lamport underflow

## Question
Can an unprivileged attacker, through a builtin-program instruction in a transaction from an unprivileged fee-payer, reach `lib::get_precompiles` and drive a System program transfer/withdraw path to underflow or double-count lamports, so that the invariant "System program conserves lamports across every instruction" is violated, leading to Loss of Funds?

## Target
- File/function: `precompiles/src/lib.rs` -> `get_precompiles`
- Entrypoint: a builtin-program instruction in a transaction from an unprivileged fee-payer
- Attacker controls: the accounts and lamport amount in a SystemInstruction it submits
- Exploit idea: Drive a System program transfer/withdraw path to underflow or double-count lamports.
- Invariant to test: System program conserves lamports across every instruction.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test submitting the builtin/precompile instruction and assert the authorization/accounting invariant holds.
