# Q710: find_bank_vault_authority_pda: transfer-hook or mint capability probe can be bypassed by account substitution [a-withdraw-path-that-uses] [family-binding]

## Question
Can an unprivileged attacker call `juplend_withdraw` with a withdraw path that uses the right vault but wrong authority context so `find_bank_vault_authority_pda` misprobes a mint capability because of account substitution, violating `vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers` and causing `Critical: unauthorized withdrawal of protocol-controlled assets`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_authority_pda`
- Entrypoint: `juplend_withdraw`
- Attacker controls: a withdraw path that uses the right vault but wrong authority context
- Exploit idea: Even though admin listing choices are out of scope, a public bug in capability probing or enforcement remains in scope if it affects live banks. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: vault-authority PDAs must be unique to the exact bank and vault family and cannot authorize foreign transfers
- Expected Immunefi impact: Critical: unauthorized withdrawal of protocol-controlled assets
- Fast validation: Supply alternate mint/account combos around a live path and assert the utility reports capabilities only for the exact bank mint in use. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
