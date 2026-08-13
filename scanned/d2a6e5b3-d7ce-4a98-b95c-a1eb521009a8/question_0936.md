# Q936: calculate_post_fee_spl_deposit_amount: vault PDA helper and transfer path disagree on the canonical vault [auxiliary-token-contexts-swapped-across] [family-binding]

## Question
Can an unprivileged attacker exploit auxiliary token contexts swapped across two same-interface mints so `calculate_post_fee_spl_deposit_amount` and a downstream transfer path disagree on the canonical vault address, violating `fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once` and causing `High: protocol fee/insurance theft or bad-debt drift`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `calculate_post_fee_spl_deposit_amount`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: auxiliary token contexts swapped across two same-interface mints
- Exploit idea: A mismatch between utility derivation and runtime constraints can redirect value even if each piece seems locally correct. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: fee-adjusted token accounting in insurance and integration paths must match real token movement exactly once
- Expected Immunefi impact: High: protocol fee/insurance theft or bad-debt drift
- Fast validation: Cross-check helper output against every consuming instruction and assert only one canonical vault/address is ever accepted. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
