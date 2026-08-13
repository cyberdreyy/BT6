# Q778: calculate_pre_fee_spl_deposit_amount: fee-adjusted amount conversion can be abused across CPI boundaries [tiny-deposits-near-zero-and] [family-binding]

## Question
Can an unprivileged attacker use `juplend_deposit` with tiny deposits near zero and one-share thresholds so `calculate_pre_fee_spl_deposit_amount` applies fee-adjusted amount conversion inconsistently across CPI boundaries, violating `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: tiny deposits near zero and one-share thresholds
- Exploit idea: Audit helpers that convert pre-fee and post-fee token amounts, especially when deposits/withdrawals bridge internal and external accounting. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Fuzz fee-bearing amount conversions around boundary values and assert the internal/external ledgers reconcile exactly. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
