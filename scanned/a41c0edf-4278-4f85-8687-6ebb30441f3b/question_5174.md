# Q5174: instruction_accounts::set_executable — lamport sum violation

## Question
Can an unprivileged attacker, through a deployed SBPF program issuing a CPI, reach `instruction_accounts::set_executable` and use CPI account ordering to make pre/post lamport-sum checks pass while lamports are created or destroyed, so that the invariant "the sum of lamports across all instruction accounts is invariant across a CPI" is violated, leading to Loss of Funds?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `set_executable`
- Entrypoint: a deployed SBPF program issuing a CPI
- Attacker controls: the account set and lamport deltas across the CPI
- Exploit idea: Use CPI account ordering to make pre/post lamport-sum checks pass while lamports are created or destroyed.
- Invariant to test: the sum of lamports across all instruction accounts is invariant across a CPI.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test invoking invoke_signed with the crafted metas/seeds and assert the privilege is not escalated.
