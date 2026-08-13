# Q627: find_bank_vault_pda: utility path can silently downgrade a required strictness check [prederived-attacker-owned-candidates-that] [amount-domain]

## Question
Can an unprivileged attacker exploit prederived attacker-owned candidates that share owner/type shape so `find_bank_vault_pda` silently downgrades a strictness check required by a live instruction, breaking `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and leading to `Critical: direct theft through vault redirection`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: prederived attacker-owned candidates that share owner/type shape
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
