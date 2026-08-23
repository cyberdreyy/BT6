# Q347: invoke_context::secp256r1_instruction_for_test — writable flag escalation

## Question
Can an unprivileged attacker, through a deployed SBPF program issuing a CPI, reach `invoke_context::secp256r1_instruction_for_test` and construct a CPI that marks an account writable when the caller only holds it read-only, so that the invariant "a CPI never grants a writable flag the caller did not hold" is violated, leading to Loss of Funds?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `secp256r1_instruction_for_test`
- Entrypoint: a deployed SBPF program issuing a CPI
- Attacker controls: the writable flags on account metas passed to the CPI
- Exploit idea: Construct a CPI that marks an account writable when the caller only holds it read-only.
- Invariant to test: a CPI never grants a writable flag the caller did not hold.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test invoking invoke_signed with the crafted metas/seeds and assert the privilege is not escalated.
