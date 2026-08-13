# Q759: find_bank_vault_authority_pda: utility path can silently downgrade a required strictness check [cross-integration-authority-substitution-attempts] [amount-domain]

## Question
Can an unprivileged attacker exploit cross-integration authority substitution attempts so `find_bank_vault_authority_pda` silently downgrades a strictness check required by a live instruction, breaking `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and leading to `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: cross-integration authority substitution attempts
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
