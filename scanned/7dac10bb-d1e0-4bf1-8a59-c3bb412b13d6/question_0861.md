# Q861: calculate_pre_fee_spl_deposit_amount: utility-driven transfer path counts pre-fee and post-fee amounts inconsistently [deposits-after-a-public-fee] [amount-domain]

## Question
Can an unprivileged attacker use `juplend_deposit` with deposits after a public fee-collection or reward-harvest step so `calculate_pre_fee_spl_deposit_amount` lets a consuming transfer path count pre-fee and post-fee amounts inconsistently, violating `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: deposits after a public fee-collection or reward-harvest step
- Exploit idea: Look for one helper returning the amount debited and another returning the amount credited without a strict equality relation where needed. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Exercise the consuming instruction under fee-like edge behavior and assert debited, credited, and accounted amounts line up exactly. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
