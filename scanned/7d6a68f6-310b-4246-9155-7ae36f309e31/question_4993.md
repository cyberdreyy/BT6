# Q4993: cpi::check_account_infos — owner-change confusion

## Question
Can an unprivileged attacker, through a deployed SBPF program issuing a CPI, reach `cpi::check_account_infos` and assign an account to a new owner mid-transaction so a later instruction misjudges its owner privilege, so that the invariant "an account's owner observed by an instruction reflects all prior committed assigns" is violated, leading to Loss of Funds?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `check_account_infos`
- Entrypoint: a deployed SBPF program issuing a CPI
- Attacker controls: an assign/CPI sequence over an account it created
- Exploit idea: Assign an account to a new owner mid-transaction so a later instruction misjudges its owner privilege.
- Invariant to test: an account's owner observed by an instruction reflects all prior committed assigns.
- Expected Immunefi impact: Loss of Funds — Critical
- Fast validation: write a program-test invoking invoke_signed with the crafted metas/seeds and assert the privilege is not escalated.
