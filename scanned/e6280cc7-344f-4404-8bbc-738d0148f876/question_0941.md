# Q941: calculate_post_fee_spl_deposit_amount: vault PDA helper and transfer path disagree on the canonical vault [a-bank-whose-insurance-and] [amount-domain]

## Question
Can an unprivileged attacker exploit a bank whose insurance and liquidity vaults share similar interfaces so `calculate_post_fee_spl_deposit_amount` and a downstream transfer path disagree on the canonical vault address, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on internal amount domain vs external token amount domain reconciliation.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: a bank whose insurance and liquidity vaults share similar interfaces
- Exploit idea: A mismatch between utility derivation and runtime constraints can redirect value even if each piece seems locally correct. Focus specifically on internal amount domain vs external token amount domain reconciliation.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Cross-check helper output against every consuming instruction and assert only one canonical vault/address is ever accepted. Fuzz one-unit and boundary values and assert debited, credited, and accounted amounts all reconcile exactly.
