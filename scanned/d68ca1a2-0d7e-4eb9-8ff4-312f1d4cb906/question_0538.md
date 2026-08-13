# Q538: find_bank_vault_pda: mint/account probing helper can be steered to the wrong token context [candidate-vaults-from-another-integration] [family-binding]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with candidate vaults from another integration family so `find_bank_vault_pda` probes or caches the wrong token context, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: candidate vaults from another integration family
- Exploit idea: Helpers like token-mint selection and transfer-hook probing must bind tightly to the bank and vault actually being used. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Supply same-owner or same-interface token accounts from another mint and assert helpers never approve the wrong token context. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
