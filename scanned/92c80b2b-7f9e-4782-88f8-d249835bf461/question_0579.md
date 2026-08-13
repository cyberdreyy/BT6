# Q579: find_bank_vault_pda: transfer-hook or mint capability probe can be bypassed by account substitution [prederived-attacker-owned-candidates-that] [amount-domain]

## Question
Can an unprivileged attacker call `kamino_withdraw` with prederived attacker-owned candidates that share owner/type shape so `find_bank_vault_pda` misprobes a mint capability because of account substitution, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: prederived attacker-owned candidates that share owner/type shape
- Exploit idea: Even though admin listing choices are out of scope, a public bug in capability probing or enforcement remains in scope if it affects live banks. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Supply alternate mint/account combos around a live path and assert the utility reports capabilities only for the exact bank mint in use. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
