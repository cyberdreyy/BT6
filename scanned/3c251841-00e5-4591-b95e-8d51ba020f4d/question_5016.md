# Q5016: cpi::transaction_with_one_readonly_instruction_account — instruction-stack depth bypass

## Question
Can an unprivileged attacker, through a deployed SBPF program issuing a CPI, reach `cpi::transaction_with_one_readonly_instruction_account` and exceed or corrupt the invoke-stack depth accounting to recurse beyond the CPI depth limit, so that the invariant "CPI depth never exceeds the fixed limit enforced by InvokeContext" is violated, leading to DoS (replay stall) / Consensus?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `transaction_with_one_readonly_instruction_account`
- Entrypoint: a deployed SBPF program issuing a CPI
- Attacker controls: recursive CPI structure across programs it deploys
- Exploit idea: Exceed or corrupt the invoke-stack depth accounting to recurse beyond the CPI depth limit.
- Invariant to test: CPI depth never exceeds the fixed limit enforced by InvokeContext.
- Expected Immunefi impact: DoS (replay stall) / Consensus — High
- Fast validation: write a program-test invoking invoke_signed with the crafted metas/seeds and assert the privilege is not escalated.
