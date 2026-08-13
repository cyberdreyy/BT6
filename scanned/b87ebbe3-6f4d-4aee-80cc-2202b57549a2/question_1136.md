# Q1136: maybe_take_bank_mint: utility-derived authority selection can be reused in the wrong instruction family [mixed-insurance-and-liquidity-contexts] [family-binding]

## Question
Can an unprivileged attacker route `lending_pool_handle_bankruptcy` through `maybe_take_bank_mint` with mixed insurance and liquidity contexts in the same transaction so a utility-derived authority selection is reused in the wrong instruction family, violating `mint selection for live value-moving paths must bind to the exact bank and cannot be redirected by caller-supplied remaining accounts` and causing `High: wrong mint context causing fee drift, misrouting, or protocol loss`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `maybe_take_bank_mint`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: mixed insurance and liquidity contexts in the same transaction
- Exploit idea: Helpers reused broadly across integrations are valuable places to look for cross-family authority confusion. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: mint selection for live value-moving paths must bind to the exact bank and cannot be redirected by caller-supplied remaining accounts
- Expected Immunefi impact: High: wrong mint context causing fee drift, misrouting, or protocol loss
- Fast validation: Call each consuming instruction with cross-family helper outputs and assert none accepts a foreign-family authority. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
