# Q885: calculate_pre_fee_spl_deposit_amount: utility path can silently downgrade a required strictness check [auxiliary-token-contexts-that-alter] [amount-domain]

## Question
Can an unprivileged attacker exploit auxiliary token contexts that alter fee-adjusted behavior so `calculate_pre_fee_spl_deposit_amount` silently downgrades a strictness check required by a live instruction, breaking `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and leading to `High: phantom internal value or understated debt through fee math drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: auxiliary token contexts that alter fee-adjusted behavior
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
