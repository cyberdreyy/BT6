# Q1013: calculate_post_fee_spl_deposit_amount: utility path can silently downgrade a required strictness check [same-slot-bankruptcy-settlement-and] [amount-domain]

## Question
Can an unprivileged attacker exploit same-slot bankruptcy settlement and fee collection investigations so `calculate_post_fee_spl_deposit_amount` silently downgrades a strictness check required by a live instruction, breaking `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and leading to `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: same-slot bankruptcy settlement and fee collection investigations
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
