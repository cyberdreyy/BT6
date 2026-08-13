# Q596: find_bank_vault_pda: utility-driven transfer path counts pre-fee and post-fee amounts inconsistently [prederived-attacker-owned-candidates-that] [family-binding]

## Question
Can an unprivileged attacker use `kamino_withdraw` with prederived attacker-owned candidates that share owner/type shape so `find_bank_vault_pda` lets a consuming transfer path count pre-fee and post-fee amounts inconsistently, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: prederived attacker-owned candidates that share owner/type shape
- Exploit idea: Look for one helper returning the amount debited and another returning the amount credited without a strict equality relation where needed. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Exercise the consuming instruction under fee-like edge behavior and assert debited, credited, and accounted amounts line up exactly. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
