# Q620: find_bank_vault_pda: utility-derived authority selection can be reused in the wrong instruction family [a-replay-of-a-valid] [family-binding]

## Question
Can an unprivileged attacker route `kamino_withdraw` through `find_bank_vault_pda` with a replay of a valid vault context against a new bank so a utility-derived authority selection is reused in the wrong instruction family, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a replay of a valid vault context against a new bank
- Exploit idea: Helpers reused broadly across integrations are valuable places to look for cross-family authority confusion. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Call each consuming instruction with cross-family helper outputs and assert none accepts a foreign-family authority. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
