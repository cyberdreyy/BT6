# Q981: calculate_post_fee_spl_deposit_amount: utility-driven transfer path counts pre-fee and post-fee amounts inconsistently [same-slot-bankruptcy-settlement-and] [amount-domain]

## Question
Can an unprivileged attacker use `lending_pool_handle_bankruptcy` with same-slot bankruptcy settlement and fee collection investigations so `calculate_post_fee_spl_deposit_amount` lets a consuming transfer path count pre-fee and post-fee amounts inconsistently, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: same-slot bankruptcy settlement and fee collection investigations
- Exploit idea: Look for one helper returning the amount debited and another returning the amount credited without a strict equality relation where needed. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Exercise the consuming instruction under fee-like edge behavior and assert debited, credited, and accounted amounts line up exactly. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
