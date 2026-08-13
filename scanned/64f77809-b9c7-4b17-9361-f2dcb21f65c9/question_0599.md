# Q599: find_bank_vault_pda: utility-driven transfer path counts pre-fee and post-fee amounts inconsistently [same-slot-deposit-withdraw-around] [amount-domain]

## Question
Can an unprivileged attacker use `kamino_withdraw` with same-slot deposit/withdraw around reused auxiliary accounts so `find_bank_vault_pda` lets a consuming transfer path count pre-fee and post-fee amounts inconsistently, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: same-slot deposit/withdraw around reused auxiliary accounts
- Exploit idea: Look for one helper returning the amount debited and another returning the amount credited without a strict equality relation where needed. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Exercise the consuming instruction under fee-like edge behavior and assert debited, credited, and accounted amounts line up exactly. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
