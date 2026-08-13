# Q783: calculate_pre_fee_spl_deposit_amount: fee-adjusted amount conversion can be abused across CPI boundaries [mixed-internal-and-external-amount] [amount-domain]

## Question
Can an unprivileged attacker use `juplend_deposit` with mixed internal and external amount domains across the same call so `calculate_pre_fee_spl_deposit_amount` applies fee-adjusted amount conversion inconsistently across CPI boundaries, violating `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: mixed internal and external amount domains across the same call
- Exploit idea: Audit helpers that convert pre-fee and post-fee token amounts, especially when deposits/withdrawals bridge internal and external accounting. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Fuzz fee-bearing amount conversions around boundary values and assert the internal/external ledgers reconcile exactly. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
