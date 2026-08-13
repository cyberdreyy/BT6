# Q737: find_bank_vault_authority_pda: utility-derived authority selection can be reused in the wrong instruction family [vault-authorities-from-sibling-banks] [amount-domain]

## Question
Can an unprivileged attacker route `juplend_withdraw` through `find_bank_vault_authority_pda` with vault authorities from sibling banks in the same group so a utility-derived authority selection is reused in the wrong instruction family, violating `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and causing `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: vault authorities from sibling banks in the same group
- Exploit idea: Helpers reused broadly across integrations are valuable places to look for cross-family authority confusion. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Call each consuming instruction with cross-family helper outputs and assert none accepts a foreign-family authority. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
