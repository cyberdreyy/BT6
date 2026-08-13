# Q848: calculate_pre_fee_spl_deposit_amount: transfer-hook or mint capability probe can be bypassed by account substitution [mixed-internal-and-external-amount] [family-binding]

## Question
Can an unprivileged attacker call `juplend_deposit` with mixed internal and external amount domains across the same call so `calculate_pre_fee_spl_deposit_amount` misprobes a mint capability because of account substitution, violating `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: mixed internal and external amount domains across the same call
- Exploit idea: Even though admin listing choices are out of scope, a public bug in capability probing or enforcement remains in scope if it affects live banks. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Supply alternate mint/account combos around a live path and assert the utility reports capabilities only for the exact bank mint in use. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
