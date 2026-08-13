# Q899: calculate_post_fee_spl_deposit_amount: fee-adjusted amount conversion can be abused across CPI boundaries [token-contexts-with-edge-case] [amount-domain]

## Question
Can an unprivileged attacker use `lending_pool_handle_bankruptcy` with token contexts with edge-case fee-adjusted behavior so `calculate_post_fee_spl_deposit_amount` applies fee-adjusted amount conversion inconsistently across CPI boundaries, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: token contexts with edge-case fee-adjusted behavior
- Exploit idea: Audit helpers that convert pre-fee and post-fee token amounts, especially when deposits/withdrawals bridge internal and external accounting. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Fuzz fee-bearing amount conversions around boundary values and assert the internal/external ledgers reconcile exactly. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
