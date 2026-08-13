# Q912: calculate_post_fee_spl_deposit_amount: fee-adjusted amount conversion can be abused across CPI boundaries [mixed-internal-debt-amounts-and] [family-binding]

## Question
Can an unprivileged attacker use `lending_pool_handle_bankruptcy` with mixed internal debt amounts and external token amounts so `calculate_post_fee_spl_deposit_amount` applies fee-adjusted amount conversion inconsistently across CPI boundaries, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: mixed internal debt amounts and external token amounts
- Exploit idea: Audit helpers that convert pre-fee and post-fee token amounts, especially when deposits/withdrawals bridge internal and external accounting. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Fuzz fee-bearing amount conversions around boundary values and assert the internal/external ledgers reconcile exactly. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
