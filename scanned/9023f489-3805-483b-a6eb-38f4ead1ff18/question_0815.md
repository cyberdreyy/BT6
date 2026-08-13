# Q815: calculate_pre_fee_spl_deposit_amount: vault PDA helper and transfer path disagree on the canonical vault [mixed-internal-and-external-amount] [amount-domain]

## Question
Can an unprivileged attacker exploit mixed internal and external amount domains across the same call so `calculate_pre_fee_spl_deposit_amount` and a downstream transfer path disagree on the canonical vault address, violating `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: mixed internal and external amount domains across the same call
- Exploit idea: A mismatch between utility derivation and runtime constraints can redirect value even if each piece seems locally correct. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Cross-check helper output against every consuming instruction and assert only one canonical vault/address is ever accepted. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
