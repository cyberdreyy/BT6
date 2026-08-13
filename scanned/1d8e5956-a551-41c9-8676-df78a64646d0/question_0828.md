# Q828: calculate_pre_fee_spl_deposit_amount: amount utility treats zero or near-zero values unsafely in a live path [a-bank-with-prior-external] [family-binding]

## Question
Can an unprivileged attacker use `juplend_deposit` with a bank with prior external position state already funded so `calculate_pre_fee_spl_deposit_amount` treats zero or near-zero values unsafely in a live value-moving path, breaking `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: a bank with prior external position state already funded
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
