# Q524: find_bank_vault_pda: fee-adjusted amount conversion can be abused across CPI boundaries [a-replay-of-a-valid] [family-binding]

## Question
Can an unprivileged attacker use `kamino_withdraw` with a replay of a valid vault context against a new bank so `find_bank_vault_pda` applies fee-adjusted amount conversion inconsistently across CPI boundaries, violating `every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family` and causing `Critical: direct theft through vault redirection`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `find_bank_vault_pda`
- Entrypoint: `kamino_withdraw`
- Attacker controls: a replay of a valid vault context against a new bank
- Exploit idea: Audit helpers that convert pre-fee and post-fee token amounts, especially when deposits/withdrawals bridge internal and external accounting. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: every value-moving integration path must use the exact canonical bank vault PDA for its bank and vault family
- Expected Immunefi impact: Critical: direct theft through vault redirection
- Fast validation: Fuzz fee-bearing amount conversions around boundary values and assert the internal/external ledgers reconcile exactly. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
