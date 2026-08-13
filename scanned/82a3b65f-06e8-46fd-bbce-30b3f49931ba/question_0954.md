# Q954: calculate_post_fee_spl_deposit_amount: amount utility treats zero or near-zero values unsafely in a live path [tiny-settlement-amounts-around-zero] [family-binding]

## Question
Can an unprivileged attacker use `lending_pool_handle_bankruptcy` with tiny settlement amounts around zero/one-unit transitions so `calculate_post_fee_spl_deposit_amount` treats zero or near-zero values unsafely in a live value-moving path, breaking `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: tiny settlement amounts around zero/one-unit transitions
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
