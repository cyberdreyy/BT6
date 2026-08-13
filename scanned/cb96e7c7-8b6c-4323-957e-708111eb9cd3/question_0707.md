# Q707: find_bank_vault_authority_pda: transfer-hook or mint capability probe can be bypassed by account substitution [precomputed-attacker-favored-authority-candidates] [amount-domain]

## Question
Can an unprivileged attacker call `juplend_withdraw` with precomputed attacker-favored authority candidates so `find_bank_vault_authority_pda` misprobes a mint capability because of account substitution, violating `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and causing `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: precomputed attacker-favored authority candidates
- Exploit idea: Even though admin listing choices are out of scope, a public bug in capability probing or enforcement remains in scope if it affects live banks. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Supply alternate mint/account combos around a live path and assert the utility reports capabilities only for the exact bank mint in use. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
