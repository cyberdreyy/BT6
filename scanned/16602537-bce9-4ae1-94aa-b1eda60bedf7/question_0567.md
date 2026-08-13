# Q567: find_bank_vault_pda: amount utility treats zero or near-zero values unsafely in a live path [same-slot-deposit-withdraw-around] [amount-domain]

## Question
Can an unprivileged attacker use `kamino_withdraw` with same-slot deposit/withdraw around reused auxiliary accounts so `find_bank_vault_pda` treats zero or near-zero values unsafely in a live value-moving path, breaking `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: same-slot deposit/withdraw around reused auxiliary accounts
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
