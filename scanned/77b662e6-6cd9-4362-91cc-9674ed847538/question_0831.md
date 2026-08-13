# Q831: calculate_pre_fee_spl_deposit_amount: amount utility treats zero or near-zero values unsafely in a live path [mixed-internal-and-external-amount] [amount-domain]

## Question
Can an unprivileged attacker use `juplend_deposit` with mixed internal and external amount domains across the same call so `calculate_pre_fee_spl_deposit_amount` treats zero or near-zero values unsafely in a live value-moving path, breaking `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: mixed internal and external amount domains across the same call
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
