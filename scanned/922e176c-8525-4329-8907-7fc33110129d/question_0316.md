# Q316: invoke_context::is_deprecate_legacy_vote_ixs_active — lamport sum violation

## Question
Can an unprivileged attacker, through a deployed SBPF program issuing a CPI, reach `invoke_context::is_deprecate_legacy_vote_ixs_active` and use CPI account ordering to make pre/post lamport-sum checks pass while lamports are created or destroyed, so that the invariant "the sum of lamports across all instruction accounts is invariant across a CPI" is violated, leading to Loss of Funds?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `is_deprecate_legacy_vote_ixs_active`
- Entrypoint: a deployed SBPF program issuing a CPI
- Attacker controls: the account set and lamport deltas across the CPI
- Exploit idea: Use CPI account ordering to make pre/post lamport-sum checks pass while lamports are created or destroyed.
- Invariant to test: the sum of lamports across all instruction accounts is invariant across a CPI.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test invoking invoke_signed with the crafted metas/seeds and assert the privilege is not escalated.
