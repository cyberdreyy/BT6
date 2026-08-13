# Q562: find_bank_vault_pda: amount utility treats zero or near-zero values unsafely in a live path [two-banks-with-the-same] [family-binding]

## Question
Can an unprivileged attacker use `kamino_withdraw` with two banks with the same mint interface but different vault families so `find_bank_vault_pda` treats zero or near-zero values unsafely in a live value-moving path, breaking `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: two banks with the same mint interface but different vault families
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
