# Q793: calculate_pre_fee_spl_deposit_amount: mint/account probing helper can be steered to the wrong token context [tiny-deposits-near-zero-and] [amount-domain]

## Question
Can an unprivileged attacker invoke `juplend_deposit` with tiny deposits near zero and one-share thresholds so `calculate_pre_fee_spl_deposit_amount` probes or caches the wrong token context, violating `pre-fee and post-fee conversions must conserve value across internal accounting and external transfers` and causing `High: phantom internal value or understated debt through fee math drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_pre_fee_spl_deposit_amount`
- Entrypoint: `juplend_deposit`
- Attacker controls: tiny deposits near zero and one-share thresholds
- Exploit idea: Helpers like token-mint selection and transfer-hook probing must bind tightly to the bank and vault actually being used. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: pre-fee and post-fee conversions must conserve value across internal accounting and external transfers
- Expected Immunefi impact: High: phantom internal value or understated debt through fee math drift
- Fast validation: Supply same-owner or same-interface token accounts from another mint and assert helpers never approve the wrong token context. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
