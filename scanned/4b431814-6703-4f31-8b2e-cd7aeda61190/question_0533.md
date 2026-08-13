# Q533: find_bank_vault_pda: mint/account probing helper can be steered to the wrong token context [a-withdraw-path-that-mixes] [amount-domain]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with a withdraw path that mixes one bank with another vault context so `find_bank_vault_pda` probes or caches the wrong token context, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a withdraw path that mixes one bank with another vault context
- Exploit idea: Helpers like token-mint selection and transfer-hook probing must bind tightly to the bank and vault actually being used. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Supply same-owner or same-interface token accounts from another mint and assert helpers never approve the wrong token context. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
