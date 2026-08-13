# Q623: find_bank_vault_pda: utility-derived authority selection can be reused in the wrong instruction family [a-withdraw-after-config-or] [amount-domain]

## Question
Can an unprivileged attacker route `kamino_withdraw` through `find_bank_vault_pda` with a withdraw after config or metadata changes on a sibling bank so a utility-derived authority selection is reused in the wrong instruction family, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a withdraw after config or metadata changes on a sibling bank
- Exploit idea: Helpers reused broadly across integrations are valuable places to look for cross-family authority confusion. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Call each consuming instruction with cross-family helper outputs and assert none accepts a foreign-family authority. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
