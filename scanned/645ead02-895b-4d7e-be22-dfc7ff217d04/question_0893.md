# Q893: calculate_pre_fee_spl_deposit_amount: utility path can silently downgrade a required strictness check [deposits-after-a-public-fee] [amount-domain]

## Question
Can an unprivileged attacker exploit deposits after a public fee-collection or reward-harvest step so `calculate_pre_fee_spl_deposit_amount` silently downgrades a strictness check required by a live instruction, breaking `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and leading to `High: phantom internal value or understated debt through fee math drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: deposits after a public fee-collection or reward-harvest step
- Exploit idea: Search for helper branches that return permissive defaults when accounts or capabilities are missing or optional. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Omit or alter the relevant account/capability input and assert the consuming instruction fails closed rather than proceeding permissively. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
