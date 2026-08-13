# Q1007: calculate_post_fee_spl_deposit_amount: utility-derived authority selection can be reused in the wrong instruction family [mixed-internal-debt-amounts-and] [amount-domain]

## Question
Can an unprivileged attacker route `lending_pool_handle_bankruptcy` through `calculate_post_fee_spl_deposit_amount` with mixed internal debt amounts and external token amounts so a utility-derived authority selection is reused in the wrong instruction family, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: mixed internal debt amounts and external token amounts
- Exploit idea: Helpers reused broadly across integrations are valuable places to look for cross-family authority confusion. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Call each consuming instruction with cross-family helper outputs and assert none accepts a foreign-family authority. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
