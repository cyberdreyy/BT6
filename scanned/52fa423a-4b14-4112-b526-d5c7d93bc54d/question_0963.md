# Q963: calculate_post_fee_spl_deposit_amount: transfer-hook or mint capability probe can be bypassed by account substitution [token-contexts-with-edge-case] [amount-domain]

## Question
Can an unprivileged attacker call `lending_pool_handle_bankruptcy` with token contexts with edge-case fee-adjusted behavior so `calculate_post_fee_spl_deposit_amount` misprobes a mint capability because of account substitution, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: token contexts with edge-case fee-adjusted behavior
- Exploit idea: Even though admin listing choices are out of scope, a public bug in capability probing or enforcement remains in scope if it affects live banks. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Supply alternate mint/account combos around a live path and assert the utility reports capabilities only for the exact bank mint in use. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
