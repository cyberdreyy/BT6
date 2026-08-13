# Q800: calculate_pre_fee_spl_deposit_amount: mint/account probing helper can be steered to the wrong token context [mixed-internal-and-external-amount] [family-binding]

## Question
Can an unprivileged attacker invoke `juplend_deposit` with mixed internal and external amount domains across the same call so `calculate_pre_fee_spl_deposit_amount` probes or caches the wrong token context, violating `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: mixed internal and external amount domains across the same call
- Exploit idea: Helpers like token-mint selection and transfer-hook probing must bind tightly to the bank and vault actually being used. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Supply same-owner or same-interface token accounts from another mint and assert helpers never approve the wrong token context. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
