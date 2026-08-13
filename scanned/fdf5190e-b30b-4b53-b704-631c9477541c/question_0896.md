# Q896: calculate_pre_fee_spl_deposit_amount: utility path can silently downgrade a required strictness check [mixed-internal-and-external-amount] [family-binding]

## Question
Can an unprivileged attacker exploit mixed internal and external amount domains across the same call so `calculate_pre_fee_spl_deposit_amount` silently downgrades a strictness check required by a live instruction, breaking `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and leading to `High: phantom internal value or understated debt through fee math drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: mixed internal and external amount domains across the same call
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
