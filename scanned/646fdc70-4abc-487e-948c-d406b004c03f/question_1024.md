# Q1024: calculate_post_fee_spl_deposit_amount: utility path can silently downgrade a required strictness check [mixed-internal-debt-amounts-and] [family-binding]

## Question
Can an unprivileged attacker exploit mixed internal debt amounts and external token amounts so `calculate_post_fee_spl_deposit_amount` silently downgrades a strictness check required by a live instruction, breaking `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and leading to `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: mixed internal debt amounts and external token amounts
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
