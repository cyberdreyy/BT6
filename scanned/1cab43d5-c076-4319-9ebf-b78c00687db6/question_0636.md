# Q636: find_bank_vault_pda: utility path can silently downgrade a required strictness check [a-replay-of-a-valid] [family-binding]

## Question
Can an unprivileged attacker exploit a replay of a valid vault context against a new bank so `find_bank_vault_pda` silently downgrades a strictness check required by a live instruction, breaking `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and leading to `Critical: direct theft through vault redirection`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a replay of a valid vault context against a new bank
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
