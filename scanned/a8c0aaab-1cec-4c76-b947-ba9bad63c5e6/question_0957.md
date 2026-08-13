# Q957: calculate_post_fee_spl_deposit_amount: amount utility treats zero or near-zero values unsafely in a live path [a-bank-whose-insurance-and] [amount-domain]

## Question
Can an unprivileged attacker use `lending_pool_handle_bankruptcy` with a bank whose insurance and liquidity vaults share similar interfaces so `calculate_post_fee_spl_deposit_amount` treats zero or near-zero values unsafely in a live value-moving path, breaking `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: a bank whose insurance and liquidity vaults share similar interfaces
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
