# Q695: find_bank_vault_authority_pda: amount utility treats zero or near-zero values unsafely in a live path [cross-integration-authority-substitution-attempts] [amount-domain]

## Question
Can an unprivileged attacker use `juplend_withdraw` with cross-integration authority substitution attempts so `find_bank_vault_authority_pda` treats zero or near-zero values unsafely in a live value-moving path, breaking `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and causing `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: cross-integration authority substitution attempts
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
