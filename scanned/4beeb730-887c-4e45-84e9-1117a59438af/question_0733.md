# Q733: find_bank_vault_authority_pda: utility-driven transfer path counts pre-fee and post-fee amounts inconsistently [candidate-authorities-from-insurance-or] [amount-domain]

## Question
Can an unprivileged attacker use `juplend_withdraw` with candidate authorities from insurance or fee vault families so `find_bank_vault_authority_pda` lets a consuming transfer path count pre-fee and post-fee amounts inconsistently, violating `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and causing `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: candidate authorities from insurance or fee vault families
- Exploit idea: Look for one helper returning the amount debited and another returning the amount credited without a strict equality relation where needed. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Exercise the consuming instruction under fee-like edge behavior and assert debited, credited, and accounted amounts line up exactly. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
